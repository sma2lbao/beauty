"""P0-1 用量闭环:采集器 → 图调用边界上报 → account usage_events。"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import closing
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import quota, usage
from agent_orchestrator.services.llm import LLMReply
from agent_orchestrator.services.llm_gateway import _parse_usage
from tests.conftest import _IDP, ACCOUNT, FakeLLM, _client, _create_run, _settings
from tests.support.conversations import _create_conversation, _post_message


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


def test_usage_report_is_durable_through_outbox(tmp_path, reporter) -> None:
    """P0-3:用量先落 outbox,再由消费者至少一次投递;重投按 event_id 幂等。

    dispatcher 起在 app 的 lifespan 里,所以这里用真实 TestClient + sqlite 库:
    先把 outbox 行改回 pending 模拟"投递完但崩在 mark 之前",再断言不会二次 POST。
    reporter 夹具在用例体之前就已配置好,早于 create_app——容器 _assemble_budget
    只在 reporter() 为空时才自建,_assemble_outbox 靠它判断是否启用持久化投递。
    """
    fake = FakeLLM(
        [
            "# 计划",
            "执行结果",
            '{"summary": "通过", "verdicts": {"日程已整理": true}}',
            "# 交付物",
        ]
    )
    app = create_app(
        _settings(
            tmp_path,
            "durable.db",
            llm_enabled=True,
            usage_outbox_enabled=True,
            outbox_dispatch_interval_seconds=0.02,
        ),
        llm_client=UsageLLM(fake),
        token_verifier=_IDP.verifier(),
    )
    with TestClient(app) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT).json()
        assert run["status"] == "completed"

        # 投递在后台任务里排队执行:轮询到 4 条(plan + execute/verify/deliver)
        deadline = time.monotonic() + 5
        while len(reporter) < 4 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert [item["path"] for item in reporter] == ["/v1/usage/events"] * 4
        # event_id = <outbox 事件 id>:<样本序号>;plan 一次调用一条事件,
        # approve 的整张图(execute/verify/deliver)同属一个边界,共三个样本。
        event_ids = [item["body"]["event_id"] for item in reporter]
        prefixes = [event_id.split(":")[0] for event_id in event_ids]
        assert len(set(prefixes)) == 2
        assert all(len(prefix) == 32 for prefix in prefixes)
        assert sorted(int(event_id.split(":")[1]) for event_id in event_ids) == [
            0,
            0,
            1,
            2,
        ]
        for item in reporter:
            body = item["body"]
            assert body["run_id"] == run_id
            assert "cost_usd" in body and "billing_source" in body

        # 等 mark_dispatched / mark_processed 落库,再模拟重复投递
        time.sleep(0.2)
        with closing(sqlite3.connect(tmp_path / "durable.db")) as conn:
            assert (
                conn.execute(
                    "SELECT status FROM outbox_events WHERE event_type = ?",
                    (usage.USAGE_REPORT_EVENT,),
                ).fetchall()
                == [("dispatched",)] * 2
            )
            assert conn.execute(
                "SELECT COUNT(*) FROM processed_events WHERE consumer = ?",
                (usage.USAGE_DELIVERY_CONSUMER,),
            ).fetchone() == (2,)
            conn.execute(
                "UPDATE outbox_events SET status = 'pending', attempts = 0,"
                " dispatched_at = NULL, available_at = '2000-01-01 00:00:00.000000'"
                " WHERE event_type = ?",
                (usage.USAGE_REPORT_EVENT,),
            )
            conn.commit()

        # 重投命中 processed_events → 直接跳过,不产生重复 POST
        time.sleep(0.3)
        assert len(reporter) == 4
        with closing(sqlite3.connect(tmp_path / "durable.db")) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE status = 'dispatched'"
            ).fetchone() == (2,)


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


def test_chat_message_reports_usage_after_commit(tmp_path, reporter) -> None:
    """P0-2:非流式直答也记用量(run_id 为空),口径与流式路径一致。"""
    fake = FakeLLM(["直接答一句"])  # 显式 chat 跳过分流:只 1 次模型调用
    with _client(tmp_path, UsageLLM(fake)) as client:
        conversation_id = _create_conversation(client)
        result = _post_message(client, conversation_id, "你好", mode="chat")
        assert result["mode"] == "chat"
        assert result["reply"]["content"] == "直接答一句"

    assert [item["path"] for item in reporter] == ["/v1/usage/events"]
    body = reporter[0]["body"]
    assert body["run_id"] is None  # 直答不挂 run
    assert body["model"] == "deepseek-chat"
    assert body["prompt_tokens"] == 10
    assert body["completion_tokens"] == 5
    assert body["cost_usd"] is None
    assert body["billing_source"] == "platform"


def test_chat_usage_not_reported_when_quota_blocks(tmp_path, reporter) -> None:
    """被配额拦下的请求没有模型调用,自然也不该产生用量记录。"""
    quota.configure_client(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "monthly_tokens": 1000,
                    "max_concurrent_runs": None,
                    "used_tokens": 1000,
                },
            )
        ),
    )
    try:
        fake = FakeLLM(["直接答一句"])
        with _client(tmp_path, UsageLLM(fake)) as client:
            conversation_id = _create_conversation(client)
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "你好", "mode": "chat"},
                headers=ACCOUNT,
            )
            assert response.status_code == 429
    finally:
        quota.configure_client(base_url="", internal_secret="")

    assert reporter == []
