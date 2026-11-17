"""取消在节点边界生效(S3,见 docs/design/recovery.md §9)。

两段覆盖:
- 节点级:注入"已取消"探针后,包装层在节点开工前抛 RunCancelled,节点根本没跑;
- 集成级:图正在调模型时取消,取消在下一个安全边界停下——run 保持 cancelled,
  不写 failed/completed,也不再发起后续模型调用。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from agent_orchestrator.graph.cancellation import RunCancelled, raise_if_cancelled
from agent_orchestrator.graph.ports import CancellationPort, GraphPorts
from agent_orchestrator.graph.runtime import GRAPH_RUNTIME_KEY, GraphRuntime
from agent_orchestrator.graph.steps import instrument_node
from agent_orchestrator.main import create_app
from tests.conftest import _IDP, ACCOUNT, FakeLLM, _settings

ACCOUNT_ID = "t-1"
VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


class _AlwaysCancelled:
    """恒为"已取消"的探针:节点一开工就会被拦下。"""

    async def is_cancelled(self) -> bool:
        return True


class _NeverCancelled:
    async def is_cancelled(self) -> bool:
        return False


def _runtime(cancel: CancellationPort) -> dict:
    return {
        "configurable": {
            GRAPH_RUNTIME_KEY: GraphRuntime(ports=replace(GraphPorts(), cancel=cancel))
        }
    }


async def _assert_blocked(cancel: CancellationPort, *, ran: list[str]) -> None:
    def node(state, config=None):  # noqa: ANN001, ANN202 - 测试桩
        ran.append("node")

    wrapper = instrument_node("probe", "execute", node)
    try:
        await wrapper({"run_id": "r1"}, _runtime(cancel))
    except RunCancelled as exc:
        assert str(exc) == "run 已取消,执行在节点边界停止"
        return
    raise AssertionError("已取消的 run 不应再进节点")


def test_node_guard_stops_before_node_runs() -> None:
    ran: list[str] = []
    asyncio.run(_assert_blocked(_AlwaysCancelled(), ran=ran))
    assert ran == []


def test_node_guard_is_noop_without_cancellation() -> None:
    ran: list[str] = []

    async def node(state, config=None):  # noqa: ANN001, ANN202 - 测试桩
        ran.append("node")
        return {"ok": True}

    wrapper = instrument_node("probe", "execute", node)
    result = asyncio.run(wrapper({"run_id": "r1"}, _runtime(_NeverCancelled())))
    assert result == {"ok": True}
    assert ran == ["node"]


def test_raise_if_cancelled_reads_port() -> None:
    async def probe(cancel: CancellationPort) -> bool:
        runtime = GraphRuntime(ports=replace(GraphPorts(), cancel=cancel))
        try:
            await raise_if_cancelled(runtime)
        except RunCancelled:
            return True
        return False

    assert asyncio.run(probe(_AlwaysCancelled())) is True
    assert asyncio.run(probe(_NeverCancelled())) is False


class _BlockingLLM(FakeLLM):
    """第 block_on_call 次调用后挂住,直到测试线程放开闸门。

    用普通 threading.Event + sleep 轮询,不依赖 app 事件循环的句柄:
    测试线程能直接放行,而 worker 只是 await sleep,循环仍然空闲可处理请求。
    """

    def __init__(self, responses: list[str], *, block_on_call: int):
        super().__init__(responses)
        self.block_on_call = block_on_call
        self.reached = threading.Event()
        self.release = threading.Event()

    async def complete(self, messages, **kwargs):  # noqa: ANN001, ANN003, ANN201
        reply = await super().complete(messages, **kwargs)
        if len(self.calls) == self.block_on_call:
            self.reached.set()
            # 卡在模型调用里:此时 run 已在执行,取消应当只等下一个边界
            deadline = time.monotonic() + 10
            while not self.release.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
        return reply


def _event_types(client: TestClient, run_id: str) -> list[str]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT)
    assert resp.status_code == 200
    return [event["type"] for event in resp.json()]


def _wait_status(client: TestClient, run_id: str, expected: set[str], *, timeout=10.0):
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT)
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in expected:
            return resp.json()
        time.sleep(0.02)
    raise AssertionError(f"等待状态 {expected} 超时,当前 {status}")


def test_cancel_while_model_call_in_flight_stops_at_next_boundary(tmp_path) -> None:
    fake = _BlockingLLM(["# 计划", "执行", VERIFY_PASS, "# 交付物"], block_on_call=2)
    settings = _settings(
        tmp_path,
        "cancel-interrupt.db",
        llm_enabled=True,
        jobs_enabled=True,
        jobs_poll_interval_seconds=0.01,
    )
    app = create_app(settings, llm_client=fake, token_verifier=_IDP.verifier())
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/runs",
            json={
                "title": "周报",
                "goal": "整理本周日程",
                "acceptance_criteria": ["日程已整理"],
            },
            headers=ACCOUNT,
        )
        run_id = created.json()["id"]
        assert (
            client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT).status_code
            == 200
        )
        _wait_status(client, run_id, {"awaiting_approval"})
        assert len(fake.calls) == 1

        approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        assert approved.status_code == 200
        # worker 已经进到 execute 节点,卡在第 2 次模型调用里
        assert fake.reached.wait(10), "execute 节点没有调模型"
        calls_at_cancel = len(fake.calls)

        cancelled = client.post(
            f"/api/v1/runs/{run_id}/cancel",
            json={"reason": "改需求"},
            headers=ACCOUNT,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        fake.release.set()
        run = _wait_status(client, run_id, {"cancelled"})
        assert run["status_reason"] == "改需求"

        # 取消后不再发起新调用:第 3 次(verify)从未发生
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(fake.calls) > calls_at_cancel:
            time.sleep(0.02)
        assert len(fake.calls) == calls_at_cancel == 2

        types = _event_types(client, run_id)
        assert "run.cancelled.v1" in types
        assert "run.failed.v1" not in types
        assert "run.completed.v1" not in types
