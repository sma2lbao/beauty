"""MCP Client(M1):JSON-RPC 客户端、注册表 API 与工具规格。"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from agent_orchestrator.services.mcp_client import MCPClient
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

    asyncio.run(scenario())
