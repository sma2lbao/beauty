"""MCP 注册表 API:server CRUD、令牌 PATCH、测试连接与内网地址拦截。"""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from agent_orchestrator.services.mcp_client import MCPAuthRequired
from tests.conftest import ACCOUNT


def _create_server(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "demo",
            "url": "http://127.0.0.1:9999/mcp",
            "token": "secret-token-1234",
            "timeout_seconds": 5,
        },
        headers=ACCOUNT,
    )
    assert response.status_code == 201
    return response.json()


def test_mcp_server_patch_token_updates_static_token(client: TestClient) -> None:
    """「改用令牌」替代路径:浏览器授权走不通时用 PATCH {"token"} 接上凭证。"""
    created = _create_server(client)
    patched = client.patch(
        f"/api/v1/mcp/servers/{created['id']}",
        json={"token": "brand-new-token-9876"},
        headers=ACCOUNT,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["has_token"] is True
    assert body["token_hint"] == "9876"
    assert "brand-new-token" not in str(body)
    # 空串 = 清除已保存令牌(退回无凭证访问)
    cleared = client.patch(
        f"/api/v1/mcp/servers/{created['id']}",
        json={"token": ""},
        headers=ACCOUNT,
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_token"] is False
    assert cleared.json()["token_hint"] is None


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
        headers=ACCOUNT,
    )
    assert patched.status_code == 200
    assert patched.json()["auto_approve"] is True
    listing_after_patch = client.get("/api/v1/mcp/servers", headers=ACCOUNT).json()
    assert listing_after_patch[0]["auto_approve"] is True

    # 测试连接:用假 client,不发真实网络
    from agent_orchestrator.services import mcp_runtime

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

    original = mcp_runtime.build_client

    def fake_build(server, secret, *, transport=None):
        return FakeClient()

    mcp_runtime.build_client = fake_build  # type: ignore[assignment]
    try:
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=ACCOUNT
        )
        assert tested.status_code == 200
        assert tested.json()["ok"] is True
        assert tested.json()["tools"][0]["name"] == "echo"

        listing = client.get("/api/v1/mcp/servers", headers=ACCOUNT).json()
        assert listing[0]["tools"][0]["name"] == "echo"
        assert listing[0]["last_status"] == "ok"
    finally:
        mcp_runtime.build_client = original  # type: ignore[assignment]

    deleted = client.delete(f"/api/v1/mcp/servers/{created['id']}", headers=ACCOUNT)
    assert deleted.status_code == 204
    assert client.get("/api/v1/mcp/servers", headers=ACCOUNT).json() == []


def test_test_server_reports_auth_required(client: TestClient) -> None:
    """401 时测试端点落 last_status=auth_required,并回结构化标记给前端。"""
    created = _create_server(client)
    from agent_orchestrator.services import mcp_runtime

    class UnauthorizedClient:
        async def initialize(self) -> dict:
            raise MCPAuthRequired("MCP 服务端要求授权(HTTP 401)")

        async def list_tools(self) -> list[dict]:
            raise AssertionError("401 之后不应继续 list_tools")

        async def call_tool(self, name: str, arguments: dict) -> str:
            raise AssertionError("unreachable")

        async def aclose(self) -> None:
            return None

    original = mcp_runtime.build_client
    mcp_runtime.build_client = lambda *args, **kwargs: UnauthorizedClient()  # type: ignore[assignment]
    try:
        tested = client.post(
            f"/api/v1/mcp/servers/{created['id']}/test", headers=ACCOUNT
        )
        assert tested.status_code == 200
        body = tested.json()
        assert body["ok"] is False
        assert body["auth_required"] is True
        assert body["tools"] == []
        assert "401" in body["detail"]

        listing = client.get("/api/v1/mcp/servers", headers=ACCOUNT).json()
        assert listing[0]["last_status"] == "auth_required"
        assert "MCPAuthRequired" in (listing[0]["last_error"] or "")
    finally:
        mcp_runtime.build_client = original  # type: ignore[assignment]


def test_test_server_blocks_internal_address(client: TestClient) -> None:
    """P0-6:指向内网/元数据地址的 server 连接前被拒,且失败落库不再静默。"""
    created = client.post(
        "/api/v1/mcp/servers",
        json={
            "name": "metadata",
            "url": "http://169.254.169.254/latest/meta-data",
            "timeout_seconds": 2,
        },
        headers=ACCOUNT,
    )
    assert created.status_code == 201

    from agent_orchestrator.services import mcp_runtime
    from agent_orchestrator.services.mcp_client import MCPClient

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    def fake_build(server, secret: str, **kwargs):
        return MCPClient(server.url, transport=httpx.MockTransport(handler))

    original = mcp_runtime.build_client
    mcp_runtime.build_client = fake_build  # type: ignore[assignment]
    try:
        tested = client.post(
            f"/api/v1/mcp/servers/{created.json()['id']}/test", headers=ACCOUNT
        )
        assert tested.status_code == 200
        body = tested.json()
        assert body["ok"] is False
        assert body["tools"] == []
        assert "内网" in body["detail"]
        # 校验在发请求之前:一个字节都没出站
        assert sent == []

        listing = client.get("/api/v1/mcp/servers", headers=ACCOUNT).json()
        assert listing[0]["last_status"] == "error"
        assert "BlockedTargetError" in (listing[0]["last_error"] or "")
    finally:
        mcp_runtime.build_client = original  # type: ignore[assignment]
