"""GatewayClient 契约:工具透传、错误映射、重试与流式重试边界。"""

import json

import httpx
import pytest
from common.errors import UpstreamError

from agent_orchestrator.services.llm import GatewayClient, ToolCall


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
                            "name": "search_knowledge",
                            "arguments": '{"query": "本周日程"}',
                        },
                    }
                ],
            },
        )

    client = GatewayClient("http://test", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "search_knowledge"}}]
    try:
        reply = await client.complete([{"role": "user", "content": "hi"}], tools=tools)
    finally:
        await client.aclose()
    assert captured["tools"] == tools
    assert reply.tool_calls == (
        ToolCall(
            id="call_1", name="search_knowledge", arguments='{"query": "本周日程"}'
        ),
    )


async def test_gateway_client_maps_error_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"code": "llm.upstream_error", "message": "模型调用失败"}
        )

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=0,
    )
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

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=0,
    )
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await client.complete([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "llm.upstream_error"
    finally:
        await client.aclose()


async def test_gateway_client_retries_transient_status_then_succeeds() -> None:
    """P0-7:5xx 是可重试的临时故障,退避后第二次成功,不把 502 暴露给用户。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(
                503, json={"code": "llm.upstream_error", "message": "上游过载"}
            )
        return httpx.Response(200, json={"model": "m", "content": "重试成功"})

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    try:
        reply = await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await client.aclose()
    assert reply.content == "重试成功"
    assert len(attempts) == 2


async def test_gateway_client_retries_connect_error_then_succeeds() -> None:
    """连接类错误同样可重试;4xx 业务错误不可重试(重发也不会变对)。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("no route")
        return httpx.Response(200, json={"model": "m", "content": "ok"})

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    try:
        reply = await client.complete([{"role": "user", "content": "hi"}])
        assert reply.content == "ok"
    finally:
        await client.aclose()
    assert len(attempts) == 2


async def test_gateway_client_does_not_retry_client_error() -> None:
    """4xx 是确定性错误:重试只会放大延迟,永远不重试。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            400, json={"code": "llm.bad_request", "message": "参数不合法"}
        )

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await client.aclose()
    assert exc_info.value.code == "llm.bad_request"
    assert len(attempts) == 1


async def test_gateway_client_retries_exhausted_maps_to_502() -> None:
    """重试次数用尽仍是上游错误:最终映射 502,并且只按最后一次计指标。"""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("no route")

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await client.complete([{"role": "user", "content": "hi"}])
    finally:
        await client.aclose()
    assert exc_info.value.status == 502
    assert len(attempts) == 3  # 首次 + 2 次重试


async def test_gateway_client_stream_retries_before_first_delta() -> None:
    """流式只在开流前重试:首响应 503 → 重试成功,正文完整无重复。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(
                503, json={"code": "llm.upstream_error", "message": "上游过载"}
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"delta": "\xe4\xbd\xa0"}\n\n'
            b'data: {"delta": "\xe5\xa5\xbd"}\n\n'
            b"data: [DONE]\n\n",
        )

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    try:
        pieces = [
            piece async for piece in client.stream([{"role": "user", "content": "hi"}])
        ]
    finally:
        await client.aclose()
    assert "".join(pieces) == "你好"
    assert len(calls) == 2


class _BrokenStream(httpx.AsyncByteStream):
    """先吐一段增量,再断开连接:用来验证"已出增量绝不重试"。"""

    async def __aiter__(self):  # type: ignore[override]
        yield b'data: {"delta": "half"}\n\n'
        raise httpx.ReadError("connection lost")


async def test_gateway_client_stream_never_retries_after_first_delta() -> None:
    """增量一旦发出就不能重发,否则正文会重复:此时连"重试次数"都不该消耗。"""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_BrokenStream(),
        )

    client = GatewayClient(
        "http://test",
        transport=httpx.MockTransport(handler),
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )
    pieces: list[str] = []
    try:
        with pytest.raises(UpstreamError) as exc_info:
            async for piece in client.stream([{"role": "user", "content": "hi"}]):
                pieces.append(piece)
    finally:
        await client.aclose()
    assert pieces == ["half"]
    assert exc_info.value.code == "llm.upstream_error"
    assert len(calls) == 1


async def test_gateway_client_stream_events_delta_then_tool_calls() -> None:
    """stream_events:正文增量逐个下发,工具调用只在收尾统一成一个事件。"""
    captured: dict = {}
    frames = [
        {"delta": "先查"},
        {"delta": "一下"},
        {
            "done": True,
            "content": "先查一下",
            "model": "m",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_knowledge",
                        "arguments": '{"query": "本周日程"}',
                    },
                }
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = "".join(
            f"data: {json.dumps(frame, ensure_ascii=False)}\n\n" for frame in frames
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(body + "data: [DONE]\n\n").encode(),
        )

    client = GatewayClient("http://test", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "search_knowledge"}}]
    try:
        events = [
            event
            async for event in client.stream_events(
                [{"role": "user", "content": "hi"}], tools=tools
            )
        ]
    finally:
        await client.aclose()
    assert captured["tools"] == tools
    # 正文增量在前、工具调用在后(契约:工具只在 done 片出现)
    assert [event.text for event in events] == ["先查", "一下", ""]
    assert events[0].tool_calls == () and events[1].tool_calls == ()
    assert events[-1].tool_calls == (
        ToolCall(
            id="call_1", name="search_knowledge", arguments='{"query": "本周日程"}'
        ),
    )
