"""P0-1 用量闭环:采集器 → 图调用边界上报 → account usage_events。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from agent_orchestrator.services import usage
from agent_orchestrator.services.llm import LLMReply
from agent_orchestrator.services.llm_gateway import _parse_usage
from tests.conftest import ACCOUNT, FakeLLM, _client, _create_run


class UsageLLM:
    """包住 FakeLLM:模拟 GatewayClient——每次调用后把 usage 记进采集器。"""

    def __init__(self, inner: FakeLLM, tokens: tuple[int, int] = (10, 5)) -> None:
        self.inner = inner
        self.prompt_tokens, self.completion_tokens = tokens

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMReply:
        reply = await self.inner.complete(
            messages, model=model, tools=tools, response_format=response_format
        )
        self._record()
        return reply

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        reply = await self.inner.complete(
            messages, model=model, tools=tools, response_format=response_format
        )
        self._record()
        for char in reply.content:
            yield char

    def _record(self) -> None:
        usage.record(
            usage.UsageSample(
                model="deepseek-chat",
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                total_tokens=self.prompt_tokens + self.completion_tokens,
            )
        )


@pytest.fixture
def reporter():
    """注入式上报器:收 HTTP 记录,零网络;用例结束复位。"""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "path": request.url.path,
                "secret": request.headers.get("X-Internal-Secret"),
                "account": request.headers.get("X-Account-Id"),
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(201, json={"id": f"evt-{len(captured)}"})

    usage.configure_reporter(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )
    try:
        yield captured
    finally:
        usage.configure_reporter(base_url="", internal_secret="")


def test_collector_accumulates_samples() -> None:
    collector = usage.UsageCollector()
    token = usage.bind(collector)
    try:
        usage.record(usage.UsageSample("m", 3, 2, 5))
        usage.record(usage.UsageSample("m", 7, 1, 8))
        assert len(collector) == 2
        assert collector.total_tokens == 13
    finally:
        usage.unbind(token)
    # 解绑后不再记录(不跨请求泄漏)
    usage.record(usage.UsageSample("m", 100, 100, 200))
    assert len(collector) == 2


def test_run_reports_usage_once_per_model_call(tmp_path, reporter) -> None:
    """plan + approve 共 4 次模型调用 → 4 条 usage_events,run_id 一致。"""
    fake = FakeLLM(
        [
            "# 计划",
            "执行结果",
            '{"summary": "通过", "verdicts": {"日程已整理": true}}',
            "# 交付物",
        ]
    )
    with _client(tmp_path, UsageLLM(fake)) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        assert run["status"] == "completed"

    assert [item["path"] for item in reporter] == ["/v1/usage/events"] * 4
    assert {item["secret"] for item in reporter} == {"s3cret"}
    assert {item["account"] for item in reporter} == {"t-1"}
    for item in reporter:
        body = item["body"]
        assert body["run_id"] == run_id
        assert body["model"] == "deepseek-chat"
        assert body["prompt_tokens"] == 10
        assert body["completion_tokens"] == 5
        # 这条桩不经过真实网关,没有报价 → cost_usd 必须显式为空(未计价 ≠ 0 元)
        assert body["cost_usd"] is None
        assert body["billing_source"] == "platform"


def test_parse_usage_carries_cost_and_billing_source() -> None:
    """网关返回的 cost_usd/billing_source 必须原样进入用量样本(P0-1 成本归因)。"""
    sample = _parse_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost_usd": 0.000123,
            "billing_source": "byok",
        },
        "deepseek-chat",
    )
    assert sample is not None
    assert sample.cost_usd == pytest.approx(0.000123)
    assert sample.billing_source == "byok"

    # 非法来源归一 platform;报价缺失/脏值留空,不冒充 0 元
    fallback = _parse_usage(
        {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "cost_usd": "not-a-number",
            "billing_source": "mystery",
        },
        None,
    )
    assert fallback is not None
    assert fallback.cost_usd is None
    assert fallback.billing_source == "platform"
    assert fallback.model == "unknown"


def test_usage_report_failure_does_not_break_run(tmp_path) -> None:
    """上报是观测数据:account 挂了也只 warning,run 必须照常完成。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("account down")

    usage.configure_reporter(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )
    try:
        fake = FakeLLM(
            [
                "# 计划",
                "执行结果",
                '{"summary": "通过", "verdicts": {"日程已整理": true}}',
                "# 交付物",
            ]
        )
        with _client(tmp_path, UsageLLM(fake)) as client:
            run_id = _create_run(client)
            client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
            run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
            assert run["status"] == "completed"
    finally:
        usage.configure_reporter(base_url="", internal_secret="")


async def test_reporter_skips_empty_samples() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(201, json={"id": "x"})

    reporter = usage.UsageReporter(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )
    try:
        await reporter.report(account_id="t-1", run_id=None, samples=[])
    finally:
        await reporter.aclose()
    assert calls == []


async def test_reporter_swallows_non_http_errors() -> None:
    """非 HTTPError 也不能上抛:上报发生在 run 的 finally,抛出即判 run failed。

    回归自 R-32:禁网后异步传输层抛的是任务组包装的 ExceptionGroup,原先只捕
    httpx.HTTPError,异常逃出 _usage_boundary 把 quota 用例全带成 failed。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("transport exploded")

    reporter = usage.UsageReporter(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )
    try:
        await reporter.report(
            account_id="t-1",
            run_id="r-1",
            samples=[usage.UsageSample("m", 1, 2, 3)],
        )
    finally:
        await reporter.aclose()


def _create_conversation(client) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=ACCOUNT)
    assert resp.status_code == 201
    return resp.json()["id"]


def test_stream_chat_reports_usage_without_run(tmp_path, reporter) -> None:
    """对话路径:一次回复一条 usage(run_id 为空),triage 调用不计入。"""
    fake = FakeLLM(['{"mode": "chat"}', "你好!"])
    with _client(tmp_path, UsageLLM(fake)) as client:
        conversation_id = _create_conversation(client)
        resp = client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"content": "你好", "mode": "auto"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200

    assert [item["path"] for item in reporter] == ["/v1/usage/events"]
    assert reporter[0]["account"] == "t-1"
    body = reporter[0]["body"]
    assert body["run_id"] is None
    assert body["model"] == "deepseek-chat"
    assert body["prompt_tokens"] == 10
