"""MCP 双栈客户端行为(契约 docs/design/mcp-dual-stack.md §6)。

覆盖:auto 探测成功锁无状态栈、探测失败回落 legacy、401 不回落、
强制版本跳过/不回落、legacy 会话失效重连不回归。全部走 httpx.MockTransport,
零网络。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agent_orchestrator.services.mcp_client import (
    MCPAuthRequired,
    MCPClient,
    MCPError,
)

SERVER = "http://mcp.local/mcp"
STATELESS = "2026-07-28"
LEGACY = "2025-03-26"


def _request_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def _record(
    calls: list[tuple[str, str | None, str | None, str | None]], request: httpx.Request
) -> dict[str, Any]:
    body = _request_body(request)
    calls.append(
        (
            str(body.get("method")),
            request.headers.get("mcp-protocol-version"),
            request.headers.get("mcp-session-id"),
            request.method,
        )
    )
    return body


def _discover_ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {
                "protocolVersions": [STATELESS, LEGACY],
                "protocolVersion": STATELESS,
                "serverInfo": {"name": "beauty-moon", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}},
            },
        },
    )


def _legacy_initialize(body: dict[str, Any], session: str = "sess-1") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"Mcp-Session-Id": session},
        json={
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {"protocolVersion": LEGACY, "capabilities": {}},
        },
    )


def _tools_list(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {"tools": [{"name": "echo", "inputSchema": {}}]},
        },
    )


def test_auto_negotiates_stateless_and_never_handshakes() -> None:
    """discover 成功:只探测一次,之后每请求自带版本头、无 session、无 DELETE。"""
    calls: list[tuple[str, str | None, str | None, str | None]] = []
    deleted: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            deleted.append(True)
            return httpx.Response(204)
        body = _record(calls, request)
        method = body.get("method")
        if method == "server/discover":
            return _discover_ok(body)
        if method == "tools/list":
            return _tools_list(body)
        if method == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"content": [{"type": "text", "text": "pong"}]},
                },
            )
        raise AssertionError(f"unexpected method: {method}")

    async def scenario() -> str:
        client = MCPClient(
            SERVER, timeout_seconds=2, transport=httpx.MockTransport(handler)
        )
        try:
            tools = await client.list_tools()
            assert tools[0]["name"] == "echo"
            return await client.call_tool("echo", {"text": "hi"})
        finally:
            await client.aclose()

    assert asyncio.run(scenario()) == "pong"
    # 恰好一次 discover + 两次业务请求,没有 initialize/notifications
    assert [call[0] for call in calls] == [
        "server/discover",
        "tools/list",
        "tools/call",
    ]
    assert all(call[1] == STATELESS for call in calls)
    assert all(call[2] is None for call in calls)
    assert deleted == []


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("http_404", id="404"),
        pytest.param("http_400", id="400"),
        pytest.param("rpc_error", id="rpc-error"),
    ],
)
def test_auto_falls_back_to_legacy_when_discover_fails(failure: str) -> None:
    """探测失败(404/400/JSON-RPC error):回落 legacy 握手,不重复探测。"""
    calls: list[tuple[str, str | None, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _record(calls, request)
        method = body.get("method")
        if method == "server/discover":
            if failure == "http_404":
                return httpx.Response(404, text="not found")
            if failure == "http_400":
                return httpx.Response(400, json={"error": "bad request"})
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "error": {"code": -32601, "message": "method not found"},
                },
            )
        if method == "initialize":
            return _legacy_initialize(body)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return _tools_list(body)
        raise AssertionError(f"unexpected method: {method}")

    async def scenario() -> list[dict[str, Any]]:
        client = MCPClient(
            SERVER, timeout_seconds=2, transport=httpx.MockTransport(handler)
        )
        try:
            return await client.list_tools()
        finally:
            await client.aclose()

    tools = asyncio.run(scenario())
    assert tools[0]["name"] == "echo"
    methods = [call[0] for call in calls]
    assert methods == [
        "server/discover",
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    # 探测请求带 2026-07-28 但无会话;initialize 不带版本头;之后按协商结果回带
    assert calls[0][1] == STATELESS and calls[0][2] is None
    assert calls[1][1] is None and calls[1][2] is None
    assert calls[2][1] == LEGACY and calls[2][2] == "sess-1"
    assert calls[3][1] == LEGACY and calls[3][2] == "sess-1"


def test_auto_discover_401_raises_auth_required_without_fallback() -> None:
    """401 = 授权问题,不是版本不兼容:auto 也抛 MCPAuthRequired,不回落 legacy。"""
    calls: list[tuple[str, str | None, str | None, str | None]] = []
    challenge = 'Bearer resource_metadata="https://mcp.example/.well-known/x"'

    def handler(request: httpx.Request) -> httpx.Response:
        _record(calls, request)
        return httpx.Response(401, headers={"WWW-Authenticate": challenge})

    async def scenario() -> MCPAuthRequired:
        client = MCPClient(
            SERVER, timeout_seconds=2, transport=httpx.MockTransport(handler)
        )
        try:
            with pytest.raises(MCPAuthRequired) as caught:
                await client.initialize()
            return caught.value
        finally:
            await client.aclose()

    error = asyncio.run(scenario())
    assert error.challenge == challenge
    assert [call[0] for call in calls] == ["server/discover"]


def test_forced_legacy_skips_discover() -> None:
    """强制 2025-03-26:完全不探测,直接 legacy 握手。"""
    calls: list[tuple[str, str | None, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = _record(calls, request)
        method = body.get("method")
        if method == "initialize":
            return _legacy_initialize(body)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return _tools_list(body)
        raise AssertionError(f"unexpected method: {method}")

    async def scenario() -> None:
        client = MCPClient(
            SERVER,
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
            protocol_version=LEGACY,
        )
        try:
            await client.initialize()
            assert (await client.list_tools())[0]["name"] == "echo"
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert [call[0] for call in calls] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


def test_forced_stateless_raises_when_discover_fails() -> None:
    """强制 2026-07-28:探测失败直接 MCPError,绝不回落 initialize。"""
    calls: list[tuple[str, str | None, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        _record(calls, request)
        return httpx.Response(404, text="no stateless support")

    async def scenario() -> MCPError:
        client = MCPClient(
            SERVER,
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
            protocol_version=STATELESS,
        )
        try:
            with pytest.raises(MCPError) as caught:
                await client.initialize()
            return caught.value
        finally:
            await client.aclose()

    error = asyncio.run(scenario())
    assert "server/discover" in str(error)
    assert [call[0] for call in calls] == ["server/discover"]


def test_legacy_fallback_still_reinitializes_on_expired_session() -> None:
    """回落 legacy 后会话失效(404)仍重连一次;discover 不重复。"""
    sessions = iter(["sess-1", "sess-2"])
    inits: list[str] = []
    discover_calls: list[str] = []
    tool_calls: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200)
        body = _request_body(request)
        method = body.get("method")
        if method == "server/discover":
            discover_calls.append("discover")
            return httpx.Response(404)
        if method == "initialize":
            session = next(sessions)
            inits.append(session)
            return _legacy_initialize(body, session)
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            session = request.headers.get("mcp-session-id")
            tool_calls.append((session, request.headers.get("mcp-protocol-version")))
            if session != "sess-2":
                return httpx.Response(404, json={"error": "unknown session"})
            return _tools_list(body)
        raise AssertionError(f"unexpected method: {method}")

    async def scenario() -> list[dict[str, Any]]:
        client = MCPClient(
            SERVER, timeout_seconds=2, transport=httpx.MockTransport(handler)
        )
        try:
            return await client.list_tools()
        finally:
            await client.aclose()

    tools = asyncio.run(scenario())
    assert tools[0]["name"] == "echo"
    assert inits == ["sess-1", "sess-2"]
    assert discover_calls == ["discover"]
    assert tool_calls == [("sess-1", LEGACY), ("sess-2", LEGACY)]
