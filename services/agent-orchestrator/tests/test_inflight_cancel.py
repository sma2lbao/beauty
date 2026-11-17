"""执行期取消(S6,design/recovery.md §9):长调用不必等到跑完才停。

三件事:
- guarded 未取消时原样返回结果;
- 探针在执行中途变真时,guarded 取消被包住的协程并抛 RunCancelled,不等它跑完;
- 取消 run 时通知 tool-service 放弃在飞调用;通知失败只记日志,不影响取消结果。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from agent_orchestrator.graph.cancellation import RunCancelled, guarded
from agent_orchestrator.graph.ports import CancellationPort, GraphPorts
from agent_orchestrator.graph.runtime import GraphRuntime
from agent_orchestrator.main import create_app
from agent_orchestrator.services import run_cancel as run_cancel_service
from tests.conftest import _IDP, ACCOUNT, _settings


class _NeverCancelled:
    async def is_cancelled(self) -> bool:
        return False


class _AlwaysCancelled:
    async def is_cancelled(self) -> bool:
        return True


class _AfterFirstPoll:
    """探针第一次答"没取消",第二次才答"已取消"——模拟执行中途收到取消。"""

    def __init__(self) -> None:
        self.polls = 0

    async def is_cancelled(self) -> bool:
        self.polls += 1
        return self.polls > 1


def _runtime(cancel: CancellationPort) -> GraphRuntime:
    return GraphRuntime(ports=replace(GraphPorts(), cancel=cancel))


def test_guarded_returns_result_without_cancellation() -> None:
    async def start() -> str:
        return "done"

    assert asyncio.run(guarded(_runtime(_NeverCancelled()), start)) == "done"


def test_guarded_abandons_long_call_on_cancel() -> None:
    """取消要真打断:30 秒的等待在第一次轮询到取消后就不再等。"""

    async def scenario() -> None:
        stopped = asyncio.Event()

        async def start() -> str:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.set()
                raise
            return "late"

        started = time.monotonic()
        try:
            await guarded(_runtime(_AfterFirstPoll()), start, poll_interval=0.01)
        except RunCancelled:
            assert time.monotonic() - started < 5, "取消没有及时生效"
            assert stopped.is_set(), "被包住的协程没有被取消"
            return
        raise AssertionError("已取消的 run 不应拿到结果")

    asyncio.run(scenario())


def test_guarded_propagates_regular_errors() -> None:
    async def start() -> str:
        raise RuntimeError("上游炸了")

    async def scenario() -> str:
        try:
            await guarded(_runtime(_NeverCancelled()), start)
        except RuntimeError as exc:
            return str(exc)
        return ""

    assert asyncio.run(scenario()) == "上游炸了"


def _create_run(client: TestClient, title: str) -> str:
    created = client.post(
        "/api/v1/runs",
        json={"title": title, "goal": "试点", "acceptance_criteria": ["完成"]},
        headers=ACCOUNT,
    )
    assert created.status_code == 201
    return created.json()["id"]


def test_cancel_run_notifies_tool_service(tmp_path) -> None:
    calls: list[tuple[str, str]] = []

    async def notifier(account_id: str, run_id: str) -> None:
        calls.append((account_id, run_id))

    app = create_app(
        _settings(tmp_path, "inflight-cancel.db"), token_verifier=_IDP.verifier()
    )
    with TestClient(app) as client:
        run_id = _create_run(client, "取消通知")
        run_cancel_service.configure_tool_cancel(notifier)
        try:
            resp = client.post(
                f"/api/v1/runs/{run_id}/cancel",
                json={"reason": "停"},
                headers=ACCOUNT,
            )
        finally:
            run_cancel_service.configure_tool_cancel(None)
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    assert calls == [("t-1", run_id)]


def test_cancel_survives_notifier_failure(tmp_path) -> None:
    """通知是旁路:工具服务不可达也必须先拿到取消成功。"""

    async def broken(account_id: str, run_id: str) -> None:
        raise RuntimeError("tool-service down")

    app = create_app(
        _settings(tmp_path, "inflight-cancel-fail.db"),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client, "取消通知失败")
        run_cancel_service.configure_tool_cancel(broken)
        try:
            resp = client.post(f"/api/v1/runs/{run_id}/cancel", headers=ACCOUNT)
        finally:
            run_cancel_service.configure_tool_cancel(None)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
