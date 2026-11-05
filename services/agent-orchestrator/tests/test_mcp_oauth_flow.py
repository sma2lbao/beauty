"""MCP OAuth 发起授权、回调换 token 与 token 刷新。"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
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


# ---------------------------------------- 授权后回跳位置(/connectors)与刷新工具


def _combined_transport(auth_server: FakeAuthServer) -> httpx.MockTransport:
    """授权服务器 + 资源服务器(JSON-RPC tools/list)合一的 MockTransport。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and str(request.url) == RESOURCE_URL:
            body = json.loads(request.content)
            method = body.get("method")
            if method == "initialize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                        },
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
                                    "name": "notion-search",
                                    "description": "搜索",
                                    "inputSchema": {"type": "object"},
                                    "annotations": {"readOnlyHint": True},
                                }
                            ]
                        },
                    },
                )
            return httpx.Response(404)
        return auth_server.handler(request)

    return httpx.MockTransport(handler)


def test_authorization_next_is_signed_into_state_and_used_for_redirect(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> tuple[dict[str, Any], str]:
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
                    next_path="/connectors",
                )
            state = parse_qs(urlsplit(url).query)["state"][0]
            payload = mcp_oauth.verify_state(settings, state)
            async with session_scope(factory) as session:
                result = await mcp_oauth.complete_authorization(
                    session,
                    state=state,
                    code="auth-code-1",
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            return payload, result.redirect_path
        finally:
            await factory.engine.dispose()

    payload, redirect_path = asyncio.run(scenario())
    assert payload["next"] == "/connectors"
    assert redirect_path.startswith("/connectors?oauth=success")
    assert "server=" in redirect_path


def test_invalid_next_falls_back_to_mcp(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> tuple[dict[str, Any], str]:
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
                    # 白名单外(试图开放重定向):一律按 /mcp
                    next_path="https://evil.example/steal",
                )
            state = parse_qs(urlsplit(url).query)["state"][0]
            payload = mcp_oauth.verify_state(settings, state)
            async with session_scope(factory) as session:
                result = await mcp_oauth.complete_authorization(
                    session,
                    state=state,
                    code="auth-code-1",
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            return payload, result.redirect_path
        finally:
            await factory.engine.dispose()

    payload, redirect_path = asyncio.run(scenario())
    assert payload["next"] == "/mcp"
    assert redirect_path.startswith("/mcp?oauth=success")


def test_complete_authorization_refreshes_tools_cache(tmp_path: Path) -> None:
    """授权成功顺手拉一次 tools/list:annotations 一并落进缓存。"""
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()
    transport = _combined_transport(auth_server)

    async def scenario() -> tuple[str, list[dict[str, Any]]]:
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
                    transport=transport,
                    next_path="/connectors",
                )
            state = parse_qs(urlsplit(url).query)["state"][0]
            async with session_scope(factory) as session:
                result = await mcp_oauth.complete_authorization(
                    session,
                    state=state,
                    code="auth-code-1",
                    settings=settings,
                    secret="mcp-secret",
                    transport=transport,
                )
            async with session_scope(factory) as session:
                server = await session.get(MCPServer, server_id)
                assert server is not None
                cached = list(server.tools_cache or [])
            return result.redirect_path, cached
        finally:
            await factory.engine.dispose()

    redirect_path, cached = asyncio.run(scenario())
    assert redirect_path.startswith("/connectors?oauth=success")
    assert [tool["name"] for tool in cached] == ["notion-search"]
    assert cached[0]["annotations"] == {"readOnlyHint": True}


def test_complete_authorization_survives_tool_refresh_failure(
    tmp_path: Path,
) -> None:
    """刷新工具失败只记日志:授权本身必须成功并正常回跳。"""
    settings = _settings(tmp_path)
    auth_server = FakeAuthServer()

    async def scenario() -> str:
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
            state = parse_qs(urlsplit(url).query)["state"][0]
            async with session_scope(factory) as session:
                result = await mcp_oauth.complete_authorization(
                    session,
                    # 资源端点不在假授权服务器的路由里 → tools/list 必然失败
                    state=state,
                    code="auth-code-1",
                    settings=settings,
                    secret="mcp-secret",
                    transport=auth_server.transport,
                )
            return result.redirect_path
        finally:
            await factory.engine.dispose()

    assert asyncio.run(scenario()).startswith("/mcp?oauth=success")


def test_start_authorization_surfaces_classified_discovery_error(
    tmp_path: Path,
) -> None:
    """发现阶段失败原样冒到 API 层(502 + code/details),不被启动流程吞掉。"""
    settings = _settings(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mcp: connection refused", request=request)

    async def scenario() -> AppError:
        factory = await _database(settings)
        try:
            server_id = await _seed_server(factory)
            async with session_scope(factory) as session:
                with pytest.raises(AppError) as caught:
                    await mcp_oauth.start_authorization(
                        session,
                        "t-1",
                        server_id,
                        settings=settings,
                        secret="mcp-secret",
                        transport=httpx.MockTransport(handler),
                    )
                return caught.value
        finally:
            await factory.engine.dispose()

    error = asyncio.run(scenario())
    assert error.code == "mcp.oauth_discovery_unreachable"
    assert error.status == 502
    assert error.details["action"] == "check_network"
    # 没写授权行,也不污染 server 记录(用户可原地重试/改用令牌)
    assert error.details["resource"] == RESOURCE_URL
