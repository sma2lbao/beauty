"""MCP 浏览器 OAuth(P1):发现、动态注册、state、换 token、刷新与 401 重试。"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from common.errors import AppError, UpstreamError
from database.base import Base
from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.models.mcp import MCPServer, MCPServerAuth
from agent_orchestrator.repositories import MCPServerAuthRepository
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import mcp_oauth, mcp_runtime
from agent_orchestrator.services.mcp_client import MCPAuthRequired
from agent_orchestrator.services.mcp_crypto import decrypt_token, encrypt_token
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


# ---------------------------------------------------------------- 发现 / 注册


def test_discovery_uses_www_authenticate_hint() -> None:
    server = FakeAuthServer()
    hint = (
        f'Bearer resource_metadata="{RESOURCE_URL}'
        '/.well-known/oauth-protected-resource"'
    )

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, hint=hint, transport=server.transport, timeout=2
        )

    assert asyncio.run(scenario())["authorization_servers"] == [ISSUER]
    # 401 原文里的 resource_metadata 优先,不再猜 origin 级候选
    assert server.seen[0] == (
        "http://resource.local/.well-known/oauth-protected-resource/mcp"
    )


def test_discovery_falls_back_to_origin_well_known() -> None:
    server = FakeAuthServer()

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_protected_resource(
            "http://resource.local", transport=server.transport, timeout=2
        )

    assert asyncio.run(scenario())["resource"] == RESOURCE_URL
    assert server.seen[0] == (
        "http://resource.local/.well-known/oauth-protected-resource"
    )


def test_discovery_failure_reports_rfc9728_missing() -> None:
    server = FakeMissingMetadata()

    async def scenario() -> None:
        await mcp_oauth.discover_protected_resource(
            RESOURCE_URL, transport=server.transport, timeout=2
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_discovery_failed"


def test_authorization_server_discovery_requires_endpoints() -> None:
    server = FakeAuthServer()

    async def scenario() -> dict[str, Any]:
        return await mcp_oauth.discover_authorization_server(
            ISSUER, transport=server.transport, timeout=2
        )

    metadata = asyncio.run(scenario())
    assert metadata["token_endpoint"] == TOKEN_ENDPOINT
    assert metadata["authorization_endpoint"] == AUTHORIZE_ENDPOINT


def test_registration_unsupported_without_registration_endpoint() -> None:
    metadata = {
        "authorization_endpoint": AUTHORIZE_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
    }

    async def scenario() -> None:
        await mcp_oauth.register_client(
            metadata,
            REDIRECT_URI,
            client_name="beauty-moon agent",
            transport=httpx.MockTransport(lambda request: httpx.Response(404)),
            timeout=2,
        )

    with pytest.raises(UpstreamError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_registration_unsupported"


# ----------------------------------------------------------------------- state


def test_state_roundtrip_tamper_and_expiry(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    payload = {"tenant_id": "t-1", "server_id": "s-1", "ts": time.time()}
    signed = mcp_oauth.sign_state(settings, payload)
    assert mcp_oauth.verify_state(settings, signed)["server_id"] == "s-1"

    with pytest.raises(AppError) as bad:
        mcp_oauth.verify_state(settings, signed[:-4] + "AAAA")
    assert bad.value.code == "mcp.oauth_state_invalid"

    expired = mcp_oauth.sign_state(settings, {**payload, "ts": time.time() - 3600})
    with pytest.raises(AppError) as stale:
        mcp_oauth.verify_state(settings, expired)
    assert stale.value.code == "mcp.oauth_state_expired"


# --------------------------------------------------- 发起授权 / 回调换 token


def test_start_authorization_builds_pkce_url_and_registers_client(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> tuple[str, dict[str, Any]]:
        factory = await _database(settings)
        try:
            server_id = await _seed_server(factory)
            async with session_scope(factory) as session:
                url = await mcp_oauth.start_authorization(
                    session,
                    "t-1",
                    server_id,
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            async with session_scope(factory) as session:
                row = await MCPServerAuthRepository(session).get_for_server(
                    "t-1", server_id
                )
                assert row is not None
                snapshot = {
                    "client_id": row.client_id,
                    "client_secret": decrypt_token(
                        "mcp-secret", row.encrypted_client_secret
                    ),
                    "token_endpoint": row.token_endpoint,
                    "authorization_server": row.authorization_server,
                    "scopes": list(row.scopes or []),
                }
            return url, snapshot
        finally:
            await factory.engine.dispose()

    url, snapshot = asyncio.run(scenario())
    assert snapshot == {
        "client_id": "client-1",
        "client_secret": "client-secret-1",
        "token_endpoint": TOKEN_ENDPOINT,
        "authorization_server": ISSUER,
        "scopes": ["mcp.read"],
    }
    parsed = urlsplit(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZE_ENDPOINT
    query = parse_qs(parsed.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["client-1"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["mcp.read"]
    assert query["resource"] == [RESOURCE_URL]
    assert query["state"]


def test_complete_authorization_stores_tokens_and_redirects(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> tuple[str, str]:
        factory = await _database(settings)
        try:
            server_id = await _seed_server(factory, status="auth_required")
            async with session_scope(factory) as session:
                url = await mcp_oauth.start_authorization(
                    session,
                    "t-1",
                    server_id,
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            state = parse_qs(urlsplit(url).query)["state"][0]
            async with session_scope(factory) as session:
                result = await mcp_oauth.complete_authorization(
                    session,
                    state=state,
                    code="auth-code-1",
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
                assert result.ok is True
                row = await MCPServerAuthRepository(session).get_for_server(
                    "t-1", server_id
                )
                assert row is not None
                assert decrypt_token("mcp-secret", row.encrypted_access_token) == (
                    "access-1"
                )
                assert decrypt_token("mcp-secret", row.encrypted_refresh_token) == (
                    "refresh-1"
                )
                assert row.expires_at is not None
                assert row.last_error is None
                server = await session.get(MCPServer, server_id)
                assert server is not None and server.last_status is None
            async with session_scope(factory) as session:
                token = await mcp_oauth.access_token(
                    session,
                    "t-1",
                    server_id,
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            return result.redirect_path, token
        finally:
            await factory.engine.dispose()

    redirect_path, token = asyncio.run(scenario())
    assert redirect_path.startswith("/mcp?oauth=success")
    assert "server=" in redirect_path
    assert token == "access-1"
    form = auth_server.token_forms[0]
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["auth-code-1"]
    assert form["code_verifier"]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["resource"] == [RESOURCE_URL]
    assert form["client_secret"] == ["client-secret-1"]


def test_complete_authorization_rejects_tampered_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> None:
        factory = await _database(settings)
        try:
            await _seed_server(factory)
            async with session_scope(factory) as session:
                await mcp_oauth.complete_authorization(
                    session,
                    state="not-a-signed-state",
                    code="auth-code-1",
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
        finally:
            await factory.engine.dispose()

    with pytest.raises(AppError) as err:
        asyncio.run(scenario())
    assert err.value.code == "mcp.oauth_state_invalid"
    assert auth_server.token_forms == []


# --------------------------------------------------------------- token 刷新


def test_access_token_refreshes_expired_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> str | None:
        factory = await _database(settings)
        try:
            server_id = await _seed_server(factory)
            await _seed_auth_row(
                factory, server_id, expires_at=datetime.now(UTC) - timedelta(minutes=5)
            )
            async with session_scope(factory) as session:
                token = await mcp_oauth.access_token(
                    session,
                    "t-1",
                    server_id,
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            async with session_scope(factory) as session:
                row = await MCPServerAuthRepository(session).get_for_server(
                    "t-1", server_id
                )
                assert row is not None
                assert decrypt_token("mcp-secret", row.encrypted_access_token) == (
                    "access-1"
                )
            return token
        finally:
            await factory.engine.dispose()

    assert asyncio.run(scenario()) == "access-1"
    form = auth_server.token_forms[0]
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh-1"]


def test_access_token_is_none_when_oauth_disabled(tmp_path: Path) -> None:
    settings = _settings(tmp_path, mcp_oauth_enabled=False)

    async def scenario() -> str | None:
        factory = await _database(settings)
        try:
            server_id = await _seed_server(factory)
            await _seed_auth_row(
                factory, server_id, expires_at=datetime.now(UTC) + timedelta(hours=1)
            )
            async with session_scope(factory) as session:
                return await mcp_oauth.access_token(
                    session,
                    "t-1",
                    server_id,
                    settings=settings,
                    secret="mcp-secret",
                )
        finally:
            await factory.engine.dispose()

    assert asyncio.run(scenario()) is None


# ------------------------------------------------ 工具调用:401 → 刷新 → 重试


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


def test_read_exposes_oauth_entry_after_auth_required() -> None:
    """首次授权入口:还没授权行,但 401 已把 last_status 落成 auth_required。"""
    before = mcp_service._to_read(_server(), "mcp-secret")
    assert before.has_oauth is False and before.oauth_authorized is False

    after = mcp_service._to_read(_server("auth_required"), "mcp-secret")
    assert after.has_oauth is True
    assert after.oauth_authorized is False


def _patch_client(monkeypatch, calls: list[tuple[str, str | None]]) -> None:
    monkeypatch.setattr(
        mcp_runtime,
        "build_client",
        lambda server, secret, *, token=None, transport=None: FakeToolClient(
            token, calls
        ),
    )


def test_handler_refreshes_token_and_retries_once(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _patch_client(monkeypatch, calls)
    provider_calls: list[tuple[str, bool]] = []

    async def provider(server_id: str, force: bool) -> str | None:
        provider_calls.append((server_id, force))
        return "fresh-token" if force else "stale-token"

    reports: list[tuple[str, str]] = []

    async def report(server_id: str, detail: str) -> None:
        reports.append((server_id, detail))

    handler = mcp_service._make_http_handler(
        _server(), "echo", "mcp-secret", report, provider
    )
    assert asyncio.run(handler({"text": "hi"})) == "pong"
    assert calls == [("echo", "stale-token"), ("echo", "fresh-token")]
    assert provider_calls == [("server-1", False), ("server-1", True)]
    assert reports == []


def test_handler_without_provider_reports_auth_required(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _patch_client(monkeypatch, calls)
    reports: list[tuple[str, str]] = []

    async def report(server_id: str, detail: str) -> None:
        reports.append((server_id, detail))

    handler = mcp_service._make_http_handler(
        _server(), "echo", "mcp-secret", report, None
    )
    payload = json.loads(asyncio.run(handler({})))
    assert payload["code"] == "mcp.auth_required"
    assert calls == [("echo", None)]
    assert reports and reports[0][0] == "server-1"
    assert "401" in reports[0][1]


def test_handler_does_not_retry_when_refresh_returns_same_token(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []
    _patch_client(monkeypatch, calls)

    async def provider(server_id: str, force: bool) -> str | None:
        return "stale-token"

    handler = mcp_service._make_http_handler(
        _server(), "echo", "mcp-secret", None, provider
    )
    payload = json.loads(asyncio.run(handler({})))
    assert payload["code"] == "mcp.auth_required"
    assert calls == [("echo", "stale-token")]


# ------------------------------------------------------- BFF 内部回调端点


def test_internal_callback_requires_internal_secret(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path, internal_auth_secret="dev-internal-secret"))
    with TestClient(app) as client:
        denied = client.post(
            "/internal/mcp/oauth/callback",
            json={"state": "x", "code": "y"},
            headers={"X-Internal-Secret": "wrong"},
        )
        assert denied.status_code == 401
        assert denied.json()["code"] == "internal.forbidden"


def test_internal_callback_returns_error_redirect_data_for_bad_state(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path, internal_auth_secret="dev-internal-secret"))
    with TestClient(app) as client:
        resp = client.post(
            "/internal/mcp/oauth/callback",
            json={"state": "bad-state", "code": "auth-code"},
            headers={"X-Internal-Secret": "dev-internal-secret"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["redirect_path"].startswith("/mcp?oauth=error")
        assert "state" in unquote(body["redirect_path"])
