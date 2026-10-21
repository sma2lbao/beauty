"""MCP Client(M1):JSON-RPC 客户端、注册表 API 与工具规格。"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.services.mcp_client import MCPAuthRequired, MCPClient
from tests.conftest import TENANT


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


def _create_server(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "demo",
            "url": "http://127.0.0.1:9999/mcp",
            "token": "secret-token-1234",
            "timeout_seconds": 5,
        },
        headers=TENANT,
    )
    assert response.status_code == 201
    return response.json()


def test_mcp_server_crud_and_test(client: TestClient) -> None:
    created = _create_server(client)
    assert created["has_token"] is True
    assert created["token_hint"] == "1234"
    assert "secret-token" not in str(created)
    assert created["tools"] == []
    # P2 新字段:默认是 http 传输,stdio 专属字段为空
    assert created["transport"] == "http"
    assert created["command"] is None
    assert created["args"] == []
    assert created["runtime"] == "python"
    assert created["image"] is None
    assert created["has_env"] is False
    assert created["env_keys"] == []
    # 免确认执行默认关:所有 MCP 工具仍逐次进确认闸
    assert created["auto_approve"] is False

    # PATCH auto_approve:可信 server 的工具不再逐次确认(tool_specs 里体现)
    patched = client.patch(
        f"/api/v1/mcp/servers/{created['id']}",
        json={"auto_approve": True},
        headers=TENANT,
    )
    assert patched.status_code == 200
    assert patched.json()["auto_approve"] is True
    listing_after_patch = client.get("/api/v1/mcp/servers", headers=TENANT).json()
    assert listing_after_patch[0]["auto_approve"] is True

    # 测试连接:用假 client,不发真实网络
    from agent_orchestrator.services import mcp as mcp_service

    class FakeClient:
        async def initialize(self) -> dict:
            return {}

        async def list_tools(self) -> list[dict]:
            return [
                {
                    "name": "echo",
                    "description": "回显",
                    "inputSchema": {"type": "object"},
                }
            ]

        async def call_tool(self, name: str, arguments: dict) -> str:
            return json.dumps({"echo": arguments})

        async def aclose(self) -> None:
            return None

    original = mcp_service.build_client

    def fake_build(server, secret, *, transport=None):
        return FakeClient()

    mcp_service.build_client = fake_build  # type: ignore[assignment]
    try:
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=TENANT
        )
        assert tested.status_code == 200
        assert tested.json()["ok"] is True
        assert tested.json()["tools"][0]["name"] == "echo"

        listing = client.get("/api/v1/mcp/servers", headers=TENANT).json()
        assert listing[0]["tools"][0]["name"] == "echo"
        assert listing[0]["last_status"] == "ok"
    finally:
        mcp_service.build_client = original  # type: ignore[assignment]

    deleted = client.delete(f"/api/v1/mcp/servers/{created['id']}", headers=TENANT)
    assert deleted.status_code == 204
    assert client.get("/api/v1/mcp/servers", headers=TENANT).json() == []


def test_tool_specs_namespace_and_handler(monkeypatch) -> None:
    import asyncio

    from agent_orchestrator.models.mcp import MCPServer
    from agent_orchestrator.services import mcp as mcp_service

    server = MCPServer(
        tenant_id="t",
        name="Demo Server",
        url="http://mcp.local/mcp",
        encrypted_token=None,
        token_hint=None,
        enabled=True,
        tools_allowlist=["echo"],
        tools_cache=[
            {"name": "echo", "description": "回显", "inputSchema": {}},
            {"name": "blocked", "description": "不在白名单", "inputSchema": {}},
        ],
        timeout_seconds=2,
    )

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [server]

    class FakeClient:
        async def initialize(self) -> dict:
            return {}

        async def call_tool(self, name: str, arguments: dict) -> str:
            return f"{name}:{arguments.get('text')}"

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)
    monkeypatch.setattr(
        mcp_service, "build_client", lambda *args, **kwargs: FakeClient()
    )

    async def scenario() -> None:
        specs = await mcp_service.tool_specs(None, "t", secret="x")  # type: ignore[arg-type]
        assert [spec.name for spec in specs] == ["mcp__demo_server__echo"]
        assert specs[0].requires_confirmation is True
        assert await specs[0].handler({"text": "hi"}) == "echo:hi"

        # auto_approve=True 的 server:工具免确认(对话路径才可能执行)
        server.auto_approve = True
        trusted = await mcp_service.tool_specs(None, "t", secret="x")  # type: ignore[arg-type]
        assert [spec.requires_confirmation for spec in trusted] == [False]

    asyncio.run(scenario())


def test_test_server_reports_auth_required(client: TestClient) -> None:
    """401 时测试端点落 last_status=auth_required,并回结构化标记给前端。"""
    created = _create_server(client)
    from agent_orchestrator.services import mcp as mcp_service

    class UnauthorizedClient:
        async def initialize(self) -> dict:
            raise MCPAuthRequired("MCP 服务端要求授权(HTTP 401)")

        async def list_tools(self) -> list[dict]:
            raise AssertionError("401 之后不应继续 list_tools")

        async def call_tool(self, name: str, arguments: dict) -> str:
            raise AssertionError("unreachable")

        async def aclose(self) -> None:
            return None

    original = mcp_service.build_client
    mcp_service.build_client = lambda *args, **kwargs: UnauthorizedClient()  # type: ignore[assignment]
    try:
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=TENANT
        )
        assert tested.status_code == 200
        body = tested.json()
        assert body["ok"] is False
        assert body["auth_required"] is True
        assert body["tools"] == []
        assert "401" in body["detail"]

        listing = client.get("/api/v1/mcp/servers", headers=TENANT).json()
        assert listing[0]["last_status"] == "auth_required"
        assert "MCPAuthRequired" in (listing[0]["last_error"] or "")
    finally:
        mcp_service.build_client = original  # type: ignore[assignment]


def test_tool_handler_401_returns_structured_error_and_marks(monkeypatch) -> None:
    """调用期 401:回喂结构化错误(不执行),并尽力上报落 last_status。"""
    import asyncio

    from agent_orchestrator.models.mcp import MCPServer
    from agent_orchestrator.services import mcp as mcp_service

    server = MCPServer(
        id="srv-1",
        tenant_id="t",
        name="Demo Server",
        url="http://mcp.local/mcp",
        encrypted_token=None,
        token_hint=None,
        enabled=True,
        tools_allowlist=[],
        tools_cache=[{"name": "echo", "description": "", "inputSchema": {}}],
        timeout_seconds=2,
    )

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [server]

    class UnauthorizedClient:
        async def initialize(self) -> dict:
            return {}

        async def call_tool(self, name: str, arguments: dict) -> str:
            raise MCPAuthRequired("MCP 服务端要求授权(HTTP 401)")

        async def aclose(self) -> None:
            return None

    marks: list[tuple[str, str]] = []

    async def mark(server_id: str, detail: str) -> None:
        marks.append((server_id, detail))
        # 标记是尽力而为:即使写库失败也不能影响工具返回
        raise RuntimeError("database is locked")

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)
    monkeypatch.setattr(
        mcp_service, "build_client", lambda *args, **kwargs: UnauthorizedClient()
    )

    async def scenario() -> None:
        specs = await mcp_service.tool_specs(
            None,  # type: ignore[arg-type]
            "t",
            secret="x",
            on_auth_required=mark,
        )
        payload = json.loads(await specs[0].handler({}))
        assert payload["code"] == "mcp.auth_required"
        assert "401" in payload["detail"]
        assert payload["hint"]
        assert marks == [("srv-1", "MCP 服务端要求授权(HTTP 401)")]

    asyncio.run(scenario())
