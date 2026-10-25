"""MCP 浏览器 OAuth 测试共享夹具:假授权服务器、种子数据与 401 客户端替身。"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from database.base import Base
from database.session import create_session_factory, session_scope

from agent_orchestrator.models.mcp import MCPServer, MCPServerAuth
from agent_orchestrator.services import mcp_runtime
from agent_orchestrator.services.mcp_client import MCPAuthRequired
from agent_orchestrator.services.mcp_crypto import encrypt_token
from tests.conftest import _settings as base_settings

RESOURCE_URL = "http://resource.local/mcp"
ISSUER = "http://as.local"
REDIRECT_URI = "http://bff.local/api/mcp/oauth/callback"
AUTHORIZE_ENDPOINT = f"{ISSUER}/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/token"
REGISTRATION_ENDPOINT = f"{ISSUER}/register"

PRM = {
    "resource": RESOURCE_URL,
    "authorization_servers": [ISSUER],
    "scopes_supported": ["mcp.read"],
}
AS_METADATA = {
    "issuer": ISSUER,
    "authorization_endpoint": AUTHORIZE_ENDPOINT,
    "token_endpoint": TOKEN_ENDPOINT,
    "registration_endpoint": REGISTRATION_ENDPOINT,
    "scopes_supported": ["mcp.read", "mcp.write"],
}
TOKEN_PAYLOAD = {
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "mcp.read",
}


class FakeAuthServer:
    """假授权服务器:PRM / AS 元数据 / 动态注册 / token 端点,记录所有请求。"""

    def __init__(
        self,
        *,
        prm: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        registration: dict[str, Any] | None = None,
        token: dict[str, Any] | None = None,
        token_status: int = 200,
    ) -> None:
        self.prm = PRM if prm is None else prm
        self.metadata = AS_METADATA if metadata is None else metadata
        self.registration = (
            {"client_id": "client-1", "client_secret": "client-secret-1"}
            if registration is None
            else registration
        )
        self.token = TOKEN_PAYLOAD if token is None else token
        self.token_status = token_status
        self.seen: list[str] = []
        self.token_forms: list[dict[str, list[str]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.seen.append(url)
        path = urlsplit(url).path
        if request.method == "GET" and "oauth-protected-resource" in path:
            return httpx.Response(200, json=self.prm)
        if request.method == "GET" and "oauth-authorization-server" in path:
            return httpx.Response(200, json=self.metadata)
        if request.method == "POST" and path == "/register":
            return httpx.Response(201, json=self.registration)
        if request.method == "POST" and path == "/token":
            self.token_forms.append(parse_qs(request.content.decode()))
            return httpx.Response(self.token_status, json=self.token)
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeMissingMetadata(FakeAuthServer):
    """只回 404 的授权服务器(用来测发现失败)。"""

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(str(request.url))
        return httpx.Response(404)


def _settings(tmp_path: Path, **overrides: Any):
    defaults: dict[str, Any] = {
        "mcp_oauth_enabled": True,
        "mcp_oauth_redirect_uri": REDIRECT_URI,
        "mcp_oauth_state_secret": "state-secret",
        "mcp_oauth_timeout_seconds": 2.0,
    }
    defaults.update(overrides)
    return base_settings(tmp_path, "oauth.db", **defaults)


async def _database(settings):
    factory = create_session_factory(settings.database_url)
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return factory


async def _seed_server(factory, *, status: str | None = None) -> str:
    async with session_scope(factory) as session:
        server = MCPServer(
            tenant_id="t-1",
            name="远程 MCP",
            url=RESOURCE_URL,
            transport="http",
            enabled=True,
            auto_approve=True,
            timeout_seconds=5,
            args=[],
            tools_allowlist=[],
            tools_cache=[],
        )
        if status is not None:
            server.last_status = status
        session.add(server)
        await session.flush()
        return server.id


async def _seed_auth_row(
    factory, server_id: str, *, expires_at: datetime, access: str = "old-access"
) -> None:
    async with session_scope(factory) as session:
        session.add(
            MCPServerAuth(
                tenant_id="t-1",
                server_id=server_id,
                authorization_server=ISSUER,
                token_endpoint=TOKEN_ENDPOINT,
                client_id="client-1",
                encrypted_client_secret=encrypt_token("mcp-secret", "client-secret-1"),
                redirect_uri=REDIRECT_URI,
                encrypted_access_token=encrypt_token("mcp-secret", access),
                encrypted_refresh_token=encrypt_token("mcp-secret", "refresh-1"),
                token_type="Bearer",
                scopes=["mcp.read"],
                expires_at=expires_at,
            )
        )


class FakeToolClient:
    """只认 fresh-token 的 MCP 客户端:旧 token 一律 401。"""

    def __init__(self, token: str | None, calls: list[tuple[str, str | None]]) -> None:
        self.token = token
        self.calls = calls

    async def initialize(self) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, self.token))
        if self.token != "fresh-token":
            raise MCPAuthRequired("服务端要求授权(HTTP 401)", challenge="Bearer")
        return "pong"

    async def aclose(self) -> None:
        return None


def _server(status: str | None = None) -> MCPServer:
    server = MCPServer(
        id="server-1",
        tenant_id="t-1",
        name="远程 MCP",
        url=RESOURCE_URL,
        transport="http",
        enabled=True,
        auto_approve=True,
        timeout_seconds=5,
    )
    server.last_status = status
    server.created_at = datetime.now(UTC)
    return server


def _patch_client(monkeypatch, calls: list[tuple[str, str | None]]) -> None:
    monkeypatch.setattr(
        mcp_runtime,
        "build_client",
        lambda server, secret, *, token=None, transport=None: FakeToolClient(
            token, calls
        ),
    )
