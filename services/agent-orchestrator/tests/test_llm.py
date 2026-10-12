"""LLM 接入:FakeLLM 全生命周期、失败 → failed 终态、工具循环、GatewayClient 契约。"""

import json

import httpx
import pytest
from common.errors import UpstreamError

from agent_orchestrator.services.llm import GatewayClient, LLMReply, ToolCall
from tests.conftest import (
    TENANT,
    FakeConnector,
    FakeLLM,
    _client,
    _create_run,
    _tool_client,
)


def test_llm_plan_and_execute_flow(tmp_path) -> None:
    verify_pass = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(
        [
            "# 执行计划(LLM)\n- [x] 由模型生成",
            "执行结果:邮件已汇总",
            verify_pass,
            "# 周报\n\n本周邮件已汇总",
        ]
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

        assert len(fake.calls) == 4  # plan / execute / verify / deliver 各一次
        assert "汇总本周邮件" in fake.calls[0][1]["content"]  # 目标进了 plan 提示词
        assert "周报" in fake.calls[0][1]["content"]


def test_plan_llm_failure_marks_run_failed(tmp_path) -> None:
    fake = FakeLLM([], fail_on_call=1)
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        plan = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).json()
        assert plan["status"] == "failed"

        run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()
        assert "模型挂了" in run["status_reason"]

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
        assert "模型挂了" in run["status_reason"]

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
        reply = await client.complete([{"role": "user", "content": "hi"}])
        assert reply.content == "你好"
        assert reply.tool_calls == ()
    finally:
        await client.aclose()


async def test_gateway_client_forwards_tools_and_parses_calls() -> None:
    """tools 透传进请求体;网关嵌套 tool_calls → 扁平 ToolCall。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "m",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "fetch_emails",
                            "arguments": '{"limit": 3}',
                        },
                    }
                ],
            },
        )

    client = GatewayClient("http://test", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "fetch_emails"}}]
    try:
        reply = await client.complete([{"role": "user", "content": "hi"}], tools=tools)
    finally:
        await client.aclose()
    assert captured["tools"] == tools
    assert reply.tool_calls == (
        ToolCall(id="call_1", name="fetch_emails", arguments='{"limit": 3}'),
    )


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
            "# 周报\n\n第二轮补齐后交付",
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
        assert len(fake.calls) == 6  # plan + (execute, verify) × 2 + deliver


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
        assert "验收未通过" in run["status_reason"]
        assert "已返工 3 轮" in run["status_reason"]
        assert "邮件已汇总" in run["status_reason"]

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
        assert "验收输出无法解析" in run["status_reason"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        failed = next(e for e in events if e["type"] == "run.failed.v1")
        assert failed["payload"]["stage"] == "execute"  # 图调用边界兜底


def test_execute_tool_loop_feeds_result_back(tmp_path) -> None:
    """execute 走 function-calling:工具结果回喂模型,调用落 run.tool.called.v1。"""
    tool_reply = LLMReply(
        content="",
        tool_calls=(ToolCall(id="call_1", name="list_connections", arguments="{}"),),
    )
    final = "已发现 1 个连接:me@qq.com"
    verify_pass = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(
        ["# 执行计划(LLM)", tool_reply, final, verify_pass, "# 连接清单交付物"]
    )
    connector = FakeConnector()
    with _tool_client(tmp_path, fake, connector) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        # 第 2 个 execute 轮次的消息里带着工具结果(role=tool)
        tool_messages = [m for m in fake.calls[2] if m["role"] == "tool"]
        assert tool_messages and "conn-1" in tool_messages[0]["content"]
        # 工具只在 execute 下发:plan 那次为 None
        assert fake.tools_seen[0] is None
        assert fake.tools_seen[1][0]["function"]["name"] == "list_connections"
        assert connector.calls == [("list_connections", "t-1")]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        types = [e["type"] for e in events]
        assert "run.tool.called.v1" in types
        tool_event = next(e for e in events if e["type"] == "run.tool.called.v1")
        assert tool_event["payload"]["name"] == "list_connections"
        assert tool_event["payload"]["ok"] is True
        # 事件顺序:工具调用记在终态之前
        assert types.index("run.tool.called.v1") < types.index("run.completed.v1")


def test_tool_error_fed_back_to_model(tmp_path) -> None:
    """工具执行失败不炸图:错误文本回喂模型,事件记为 ok=false。"""
    tool_reply = LLMReply(
        content="",
        tool_calls=(
            ToolCall(
                id="call_1",
                name="fetch_emails",
                arguments='{"connection_id": "conn-1"}',
            ),
        ),
    )
    final = "读取失败:连接器异常,已如实报告"
    verify_pass = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(["# 计划", tool_reply, final, verify_pass, "# 失败说明交付物"])
    connector = FakeConnector(fail=True)
    with _tool_client(tmp_path, fake, connector) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"  # 工具坏 ≠ run 坏(§6.1)

        tool_messages = [m for m in fake.calls[2] if m["role"] == "tool"]
        assert "error" in tool_messages[-1]["content"]

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        tool_event = next(e for e in events if e["type"] == "run.tool.called.v1")
        assert tool_event["payload"]["ok"] is False
        assert "连接器挂了" in tool_event["payload"]["detail"]
