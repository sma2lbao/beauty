"""P0-1 用量闭环:采集器 → 图调用边界上报 → account usage_events。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from agent_orchestrator.services import usage
from agent_orchestrator.services.llm import LLMReply
from tests.conftest import TENANT, FakeLLM, _client, _create_run


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
    ) -> LLMReply:
        reply = await self.inner.complete(messages, model=model, tools=tools)
        self._record()
        return reply

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        reply = await self.inner.complete(messages, model=model, tools=tools)
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
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

    assert [item["path"] for item in reporter] == ["/v1/usage/events"] * 4
    assert {item["secret"] for item in reporter} == {"s3cret"}
    for item in reporter:
        body = item["body"]
        assert body["run_id"] == run_id
        assert body["model"] == "deepseek-chat"
        assert body["prompt_tokens"] == 10
        assert body["completion_tokens"] == 5


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
            client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
            run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
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
        await reporter.report(tenant_id="t-1", run_id=None, samples=[])
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
            tenant_id="t-1",
            run_id="r-1",
            samples=[usage.UsageSample("m", 1, 2, 3)],
        )
    finally:
        await reporter.aclose()


def _create_conversation(client) -> str:
    resp = client.post("/api/v1/conversations", json={}, headers=TENANT)
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
            headers=TENANT,
        )
        assert resp.status_code == 200

    assert [item["path"] for item in reporter] == ["/v1/usage/events"]
    body = reporter[0]["body"]
    assert body["run_id"] is None
    assert body["model"] == "deepseek-chat"
    assert body["prompt_tokens"] == 10
