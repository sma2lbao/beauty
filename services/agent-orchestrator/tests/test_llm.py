"""LLM 接入:FakeLLM 全生命周期、失败 → failed 终态、GatewayClient 契约。"""

import httpx
import pytest
from common.errors import UpstreamError
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.services.llm import GatewayClient

TENANT = {"X-Tenant-Id": "t-1"}


class FakeLLM:
    """按调用次序吐响应:第 1 次是 plan,第 2 次是 execute。"""

    def __init__(self, responses: list[str], fail_on_call: int | None = None):
        self.responses = list(responses)
        self.fail_on_call = fail_on_call
        self.calls: list[list[dict[str, str]]] = []

    async def complete(
        self, messages: list[dict[str, str]], *, model: str | None = None
    ) -> str:
        self.calls.append(messages)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("模型挂了")
        return self.responses[len(self.calls) - 1]


def _client(tmp_path, fake: FakeLLM) -> TestClient:
    settings = Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/llm.db",
        auto_create_tables=True,
        llm_enabled=True,
    )
    app = create_app(settings, llm_client=fake)
    return TestClient(app)


def _create_run(client: TestClient, goal: str = "汇总本周邮件") -> str:
    resp = client.post(
        "/api/v1/runs",
        json={"title": "周报", "goal": goal, "acceptance_criteria": ["邮件已汇总"]},
        headers=TENANT,
    )
    return resp.json()["id"]


def test_llm_plan_and_execute_flow(tmp_path) -> None:
    verify_pass = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(
        ["# 执行计划(LLM)\n- [x] 由模型生成", "执行结果:邮件已汇总", verify_pass]
    )
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        plan = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).json()
        assert plan["status"] == "awaiting_approval"
        assert plan["plan_markdown"] == "# 执行计划(LLM)\n- [x] 由模型生成"

        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"
        assert run["rounds"] == 1

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        completed = next(e for e in events if e["type"] == "run.completed.v1")
        assert completed["payload"]["results"] == [
            "执行结果:邮件已汇总",
            "验收小结:验收通过",
        ]

        assert len(fake.calls) == 3  # plan / execute / verify 各一次
        assert "汇总本周邮件" in fake.calls[0][1]["content"]  # 目标进了 plan 提示词
        assert "周报" in fake.calls[0][1]["content"]


def test_plan_llm_failure_marks_run_failed(tmp_path) -> None:
    fake = FakeLLM([], fail_on_call=1)
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        plan = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).json()
        assert plan["status"] == "failed"

        run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()
        assert "模型挂了" in run["blocked_reason"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        failed = next(e for e in events if e["type"] == "run.failed.v1")
        assert failed["payload"]["stage"] == "plan"

        # failed 是终态:plan/approve 都被状态守卫拦下(409)
        assert (
            client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code
            == 409
        )
        assert (
            client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).status_code
            == 409
        )


def test_execute_llm_failure_marks_run_failed(tmp_path) -> None:
    fake = FakeLLM(["# 计划"], fail_on_call=2)
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        assert (
            client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).json()["status"]
            == "awaiting_approval"
        )
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "failed"
        assert "模型挂了" in run["blocked_reason"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        failed = next(e for e in events if e["type"] == "run.failed.v1")
        assert failed["payload"]["stage"] == "execute"
        types = [e["type"] for e in events]
        assert types == [
            "run.created.v1",
            "run.planned.v1",
            "run.approved.v1",
            "run.failed.v1",
        ]


async def test_gateway_client_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/completions"
        return httpx.Response(
            200,
            json={"model": "deepseek/deepseek-chat", "content": "你好", "usage": None},
        )

    client = GatewayClient("http://test", transport=httpx.MockTransport(handler))
    try:
        assert await client.complete([{"role": "user", "content": "hi"}]) == "你好"
    finally:
        await client.aclose()


async def test_gateway_client_maps_error_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"code": "llm.upstream_error", "message": "模型调用失败"}
        )

    client = GatewayClient("http://test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await client.complete([{"role": "user", "content": "hi"}])
        assert exc_info.value.status == 502
        assert exc_info.value.code == "llm.upstream_error"
        assert exc_info.value.message == "模型调用失败"
    finally:
        await client.aclose()


async def test_gateway_client_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = GatewayClient("http://test", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await client.complete([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "llm.upstream_error"
    finally:
        await client.aclose()


def test_rework_loop_passes_on_second_round(tmp_path) -> None:
    """验收首轮不过 → 返工 → 第二轮通过:rounds=2,验收小结入 results(§6.1)。"""
    fail_json = '{"summary": "首轮未达标", "verdicts": {"邮件已汇总": false}}'
    pass_json = '{"summary": "已达标", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(
        [
            "# 执行计划(LLM)",
            "执行第 1 轮:汇总了部分邮件",
            fail_json,
            "执行第 2 轮:补齐汇总",
            pass_json,
        ]
    )
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"
        assert run["rounds"] == 2

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        completed = next(e for e in events if e["type"] == "run.completed.v1")
        assert "验收小结:已达标" in completed["payload"]["results"]
        assert len(fake.calls) == 5  # plan + (execute, verify) × 2


def test_rework_exhausted_marks_run_failed(tmp_path) -> None:
    """返工 3 轮仍不过:诚实进 failed,不伪装 completed;rounds 如实为 3。"""
    fail_json = '{"summary": "仍未达标", "verdicts": {"邮件已汇总": false}}'
    fake = FakeLLM(["# 计划", "执行", fail_json, "执行", fail_json, "执行", fail_json])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "failed"
        assert run["rounds"] == 3
        assert "验收未通过" in run["blocked_reason"]
        assert "已返工 3 轮" in run["blocked_reason"]
        assert "邮件已汇总" in run["blocked_reason"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        failed = next(e for e in events if e["type"] == "run.failed.v1")
        assert failed["payload"]["stage"] == "verify"
        assert failed["payload"]["rounds"] == 3
        assert len(fake.calls) == 7  # plan + (execute, verify) × 3


def test_verify_parse_error_marks_run_failed(tmp_path) -> None:
    """验收输出非 JSON:节点抛错,图调用边界统一兜底为 failed(§6.2)。"""
    fake = FakeLLM(["# 计划", "执行", "我觉得都完成了!"])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "failed"
        assert "验收输出无法解析" in run["blocked_reason"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        failed = next(e for e in events if e["type"] == "run.failed.v1")
        assert failed["payload"]["stage"] == "execute"  # 图调用边界兜底
