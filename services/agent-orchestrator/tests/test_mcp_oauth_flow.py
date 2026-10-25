"""MCP OAuth 发起授权、回调换 token 与 token 刷新。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from common.errors import AppError
from database.session import session_scope

from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.repositories import MCPServerAuthRepository
from agent_orchestrator.services import mcp_oauth
from agent_orchestrator.services.mcp_crypto import decrypt_token
from tests.support.mcp_oauth import (
    AUTHORIZE_ENDPOINT,
    ISSUER,
    REDIRECT_URI,
    RESOURCE_URL,
    TOKEN_ENDPOINT,
    FakeAuthServer,
    _database,
    _seed_auth_row,
    _seed_server,
    _settings,
)

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
