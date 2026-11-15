"""MCP Client(M1):JSON-RPC 客户端语义(session/协议版本、过期重连、401)。"""

from __future__ import annotations

import json

import httpx
import pytest

from agent_orchestrator.services.mcp_client import MCPAuthRequired, MCPClient


def _rpc_client() -> MCPClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2025-03-26", "capabilities": {}},
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "回显",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                },
            )
        if method == "tools/call":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"content": [{"type": "text", "text": "pong"}]},
                },
            )
        return httpx.Response(404)

    return MCPClient(
        "http://mcp.local/mcp",
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )


def test_mcp_client_initialize_list_and_call() -> None:
    import asyncio

    async def scenario() -> None:
        client = _rpc_client()
        await client.initialize()
        tools = await client.list_tools()
        assert tools[0]["name"] == "echo"
        assert await client.call_tool("echo", {"text": "hi"}) == "pong"
        await client.aclose()

    asyncio.run(scenario())


def test_mcp_client_forwards_session_and_protocol_headers() -> None:
    """有状态 server:initialize 之后每个请求都要回带 session 与协商版本。"""
    import asyncio

    calls: list[tuple[str | None, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        if method == "DELETE":
            calls.append(
                (
                    "DELETE",
                    request.headers.get("mcp-session-id"),
                    request.headers.get("mcp-protocol-version"),
                )
            )
            return httpx.Response(200)
        body = json.loads(request.content)
        name = body.get("method")
        calls.append(
            (
                name,
                request.headers.get("mcp-session-id"),
                request.headers.get("mcp-protocol-version"),
            )
        )
        if name == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sess-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                    },
                },
            )
        if name == "notifications/initialized":
            return httpx.Response(202)
        if name == "tools/list":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "echo", "inputSchema": {}}]},
                },
            )
        raise AssertionError(f"unexpected method: {name}")

    async def scenario() -> None:
        client = MCPClient(
            "http://mcp.local/mcp",
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
            # 该 handler 不认识 server/discover,显式锁 legacy 栈
            protocol_version="2025-03-26",
        )
        await client.initialize()
        tools = await client.list_tools()
        assert tools[0]["name"] == "echo"
        await client.aclose()

    asyncio.run(scenario())

    # initialize 不带会话;之后每个请求(含 notification)都回带 session 与协商版本
    assert calls[0] == ("initialize", None, None)
    assert calls[1] == ("notifications/initialized", "sess-1", "2025-06-18")
    assert calls[2] == ("tools/list", "sess-1", "2025-06-18")
    assert calls[3] == ("DELETE", "sess-1", "2025-06-18")


def test_mcp_client_reinitializes_on_expired_session() -> None:
    """会话过期(HTTP 404)后自动重新 initialize 并重试一次。"""
    import asyncio

    sessions = iter(["sess-1", "sess-2"])
    inits: list[str] = []
    tool_calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        name = body.get("method")
        if name == "initialize":
            session = next(sessions)
            inits.append(session)
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": session},
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                    },
                },
            )
        if name == "notifications/initialized":
            return httpx.Response(202)
        if name == "tools/list":
            session = request.headers.get("mcp-session-id")
            tool_calls.append(session)
            if session != "sess-2":
                return httpx.Response(404, json={"error": "unknown session"})
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": [{"name": "echo", "inputSchema": {}}]},
                },
            )
        raise AssertionError(f"unexpected method: {name}")

    async def scenario() -> list[dict]:
        client = MCPClient(
            "http://mcp.local/mcp",
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
        )
        await client.initialize()
        try:
            return await client.list_tools()
        finally:
            await client.aclose()

    tools = asyncio.run(scenario())
    assert tools[0]["name"] == "echo"
    assert inits == ["sess-1", "sess-2"]
    assert tool_calls == ["sess-1", "sess-2"]


def test_mcp_client_401_raises_auth_required() -> None:
    """401 = 需要授权:抛 MCPAuthRequired 并保留 WWW-Authenticate 细节。"""
    import asyncio

    challenge = (
        'Bearer resource_metadata="https://mcp.example/'
        '.well-known/oauth-protected-resource"'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={"WWW-Authenticate": challenge},
            json={"error": "unauthorized"},
        )

    async def scenario() -> MCPAuthRequired:
        client = MCPClient(
            "http://mcp.local/mcp",
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(MCPAuthRequired) as caught:
                await client.initialize()
            return caught.value
        finally:
            await client.aclose()

    error = asyncio.run(scenario())
    assert error.code == 401
    assert error.challenge == challenge
    assert "HTTP 401" in str(error)
