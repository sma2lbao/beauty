"""远程 MCP 的浏览器 OAuth 授权(PRM → AS 元数据 → DCR → PKCE → token 刷新)。

云端 agent 不能自己开浏览器,所以授权码流程这样分工:

1. 前端点「去授权」→ 本模块发现资源元数据(RFC 9728)与授权服务器元数据
   (RFC 8414 / OIDC),必要时动态注册客户端(RFC 7591),生成带 PKCE S256
   与一次性加密 state 的授权链接;
2. 用户在浏览器完成授权,授权服务器 302 到 BFF 的同源回调;
3. BFF 把 code+state 交给本服务的内部回调端点,本模块换 token、加密落库
   (mcp_server_auth),再让浏览器跳回 /mcp;
4. 之后每次调用前解析 token;过期用 refresh_token 单飞刷新,失败即标记
   needs_reauth(由调用方回 401 语义),不阻塞 run。

安全口径:access/refresh token 与 client_secret 只存密文(mcp_crypto),
永不出现在 API 响应、日志与沙箱里;state 用 HMAC 签名并带 TTL。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from common.errors import AppError, ConflictError, UpstreamError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.mcp import MCPServer, MCPServerAuth
from agent_orchestrator.repositories import (
    MCPServerAuthRepository,
    MCPServerRepository,
)
from agent_orchestrator.schemas.mcp import MCPOAuthCallbackRead
from agent_orchestrator.services.mcp_crypto import encrypt_token
from agent_orchestrator.services.mcp_oauth_discovery import _USER_AGENT as _USER_AGENT
from agent_orchestrator.services.mcp_oauth_discovery import (
    AS_METADATA_PATH as AS_METADATA_PATH,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    EXPIRY_SKEW_SECONDS as EXPIRY_SKEW_SECONDS,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    OIDC_CONFIGURATION_PATH as OIDC_CONFIGURATION_PATH,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    RESOURCE_METADATA_PATH as RESOURCE_METADATA_PATH,
)
from agent_orchestrator.services.mcp_oauth_discovery import _aware as _aware
from agent_orchestrator.services.mcp_oauth_discovery import _b64d as _b64d
from agent_orchestrator.services.mcp_oauth_discovery import _b64e as _b64e
from agent_orchestrator.services.mcp_oauth_discovery import (
    _candidate_urls as _candidate_urls,
)
from agent_orchestrator.services.mcp_oauth_discovery import _challenge as _challenge
from agent_orchestrator.services.mcp_oauth_discovery import (
    _challenge_hint as _challenge_hint,
)
from agent_orchestrator.services.mcp_oauth_discovery import _endpoint as _endpoint
from agent_orchestrator.services.mcp_oauth_discovery import _get_json as _get_json
from agent_orchestrator.services.mcp_oauth_discovery import _http_client as _http_client
from agent_orchestrator.services.mcp_oauth_discovery import (
    _maybe_decrypt as _maybe_decrypt,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    _new_verifier as _new_verifier,
)
from agent_orchestrator.services.mcp_oauth_discovery import _now as _now
from agent_orchestrator.services.mcp_oauth_discovery import (
    _refresh_locks as _refresh_locks,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    _signing_secret as _signing_secret,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    discover_authorization_server as discover_authorization_server,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    discover_protected_resource as discover_protected_resource,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    redirect_path as redirect_path,
)
from agent_orchestrator.services.mcp_oauth_discovery import (
    register_client as register_client,
)
from agent_orchestrator.services.mcp_oauth_discovery import sign_state as sign_state
from agent_orchestrator.services.mcp_oauth_discovery import verify_state as verify_state

#: 过期前多少秒就提前刷新,避免"刚好在调用途中过期"


#: 同一 server 的刷新单飞:并发调用只让一个请求去换 token


async def start_authorization(
    session: AsyncSession,
    tenant_id: str,
    server_id: str,
    *,
    settings: Settings,
    secret: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """发现 + 注册 + 签发 state,返回浏览器应跳转的授权地址。"""
    if not settings.mcp_oauth_enabled:
        raise ConflictError("mcp.oauth_disabled", "浏览器 OAuth 授权未启用")
    server = await MCPServerRepository(session).require_for_tenant(tenant_id, server_id)
    if (server.transport or "http") != "http":
        raise ConflictError("mcp.oauth_stdio_unsupported", "stdio MCP 不需要浏览器授权")
    timeout = settings.mcp_oauth_timeout_seconds
    resource_metadata = await discover_protected_resource(
        server.url,
        hint=_challenge_hint(server.last_error),
        transport=transport,
        timeout=timeout,
    )
    issuer = str(resource_metadata["authorization_servers"][0]).rstrip("/")
    metadata = await discover_authorization_server(
        issuer, transport=transport, timeout=timeout
    )
    authorization_endpoint = str(metadata["authorization_endpoint"])
    token_endpoint = str(metadata["token_endpoint"])
    redirect_uri = settings.mcp_oauth_redirect_uri

    repo = MCPServerAuthRepository(session)
    row = await repo.get_for_server(tenant_id, server_id)
    reuse = (
        row is not None
        and bool(row.client_id)
        and (row.authorization_server or "").rstrip("/") == issuer
        and row.redirect_uri == redirect_uri
    )
    if reuse:
        client_id = str(row.client_id)
        encrypted_client_secret = row.encrypted_client_secret
    else:
        registration = await register_client(
            metadata,
            redirect_uri,
            client_name=settings.mcp_oauth_client_name,
            transport=transport,
            timeout=timeout,
        )
        client_id = str(registration["client_id"])
        client_secret = registration.get("client_secret")
        encrypted_client_secret = (
            encrypt_token(secret, str(client_secret))
            if isinstance(client_secret, str) and client_secret
            else None
        )

    scopes = resource_metadata.get("scopes_supported")
    if not isinstance(scopes, list) or not scopes:
        scopes = metadata.get("scopes_supported")
    scope_list = [str(scope) for scope in scopes or [] if isinstance(scope, str)]

    if row is None:
        row = MCPServerAuth(tenant_id=tenant_id, server_id=server_id)
        await repo.add(row)
    row.authorization_server = issuer
    row.token_endpoint = token_endpoint
    row.client_id = client_id
    row.encrypted_client_secret = encrypted_client_secret
    row.redirect_uri = redirect_uri
    row.last_error = None
    if scope_list:
        row.scopes = scope_list
    if not reuse:
        # 换了客户端/授权服务器:旧 token 不再有效,清掉避免误用
        row.encrypted_access_token = None
        row.encrypted_refresh_token = None
        row.expires_at = None
        row.token_type = None

    verifier = _new_verifier()
    payload = {
        "tenant_id": tenant_id,
        "server_id": server_id,
        "ts": int(time.time()),
        "nonce": secrets.token_urlsafe(8),
        "redirect_uri": redirect_uri,
        "resource": server.url,
        "authorization_server": issuer,
        "token_endpoint": token_endpoint,
        "scopes": scope_list,
        "verifier": encrypt_token(secret, verifier),
    }
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "state": sign_state(settings, payload),
        "resource": server.url,
    }
    if scope_list:
        params["scope"] = " ".join(scope_list)
    return f"{authorization_endpoint}?{urlencode(params)}"


async def _post_token(
    token_endpoint: str,
    data: dict[str, str],
    *,
    transport: httpx.AsyncBaseTransport | None,
    timeout: float,
) -> dict[str, Any]:
    async with _http_client(transport, timeout) as client:
        try:
            response = await client.post(
                token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "mcp.oauth_token_failed", "换取 token 的请求失败"
            ) from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not isinstance(body, dict):
        error = body.get("error") if isinstance(body, dict) else None
        description = body.get("error_description") if isinstance(body, dict) else None
        raise UpstreamError(
            "mcp.oauth_token_failed",
            f"换取 token 失败(HTTP {response.status_code}"
            + (f": {error}" if isinstance(error, str) else "")
            + ")",
            details={
                "error": str(error or "")[:100],
                "error_description": str(description or "")[:200],
            },
        )
    return body


def _apply_token(row: MCPServerAuth, token: dict[str, Any], secret: str) -> str:
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        raise UpstreamError("mcp.oauth_token_failed", "授权服务器没有返回 access_token")
    row.encrypted_access_token = encrypt_token(secret, access)
    refresh = token.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        # 服务器可能不轮换 refresh_token:没返回就保留旧的
        row.encrypted_refresh_token = encrypt_token(secret, refresh)
    token_type = token.get("token_type")
    if isinstance(token_type, str) and token_type:
        row.token_type = token_type[:32]
    scope = token.get("scope")
    if isinstance(scope, str) and scope.strip():
        row.scopes = scope.split()
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        row.expires_at = _now() + timedelta(seconds=float(expires_in))
    return access


async def complete_authorization(
    session: AsyncSession,
    *,
    state: str,
    code: str,
    settings: Settings,
    secret: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MCPOAuthCallbackRead:
    """校验 state → 换 token → 加密落库;失败也返回可跳转的 redirect_path。"""
    payload = verify_state(settings, state)
    tenant_id = str(payload.get("tenant_id") or "")
    server_id = str(payload.get("server_id") or "")
    if not tenant_id or not server_id:
        raise AppError("mcp.oauth_state_invalid", "授权 state 缺少租户或 server 信息")
    server = await MCPServerRepository(session).get_for_tenant(tenant_id, server_id)
    if server is None:
        raise AppError(
            "mcp.server_not_found",
            "MCP server 已被删除，请重新注册后再授权",
            details={"mcp_server_id": server_id},
        )
    row = await MCPServerAuthRepository(session).get_for_server(tenant_id, server_id)
    if row is None or not row.client_id:
        raise AppError(
            "mcp.oauth_state_invalid", "授权记录不存在或已被删除，请重新发起授权"
        )
    token_endpoint = str(payload.get("token_endpoint") or row.token_endpoint or "")
    if not token_endpoint:
        raise AppError(
            "mcp.oauth_state_invalid", "授权记录缺少 token 端点，请重新发起授权"
        )
    verifier = _maybe_decrypt(secret, payload.get("verifier")) or ""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(
            payload.get("redirect_uri")
            or row.redirect_uri
            or settings.mcp_oauth_redirect_uri
        ),
        "client_id": str(row.client_id),
        "code_verifier": verifier,
        "resource": str(payload.get("resource") or server.url),
    }
    client_secret = _maybe_decrypt(secret, row.encrypted_client_secret)
    if client_secret:
        data["client_secret"] = client_secret
    token = await _post_token(
        token_endpoint,
        data,
        transport=transport,
        timeout=settings.mcp_oauth_timeout_seconds,
    )
    _apply_token(row, token, secret)
    row.last_error = None
    if server.last_status == "auth_required":
        server.last_status = None
        server.last_error = None
    return MCPOAuthCallbackRead(
        ok=True, redirect_path=redirect_path(ok=True, server_name=server.name)
    )


def _expired(row: MCPServerAuth) -> bool:
    expires_at = _aware(row.expires_at)
    if expires_at is None:
        return False
    return expires_at <= _now() + timedelta(seconds=EXPIRY_SKEW_SECONDS)


def _lock_for(key: str) -> asyncio.Lock:
    lock = _refresh_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[key] = lock
    return lock


async def _refresh(
    session: AsyncSession,
    server: MCPServer,
    row: MCPServerAuth,
    *,
    secret: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
) -> str | None:
    refresh_token = _maybe_decrypt(secret, row.encrypted_refresh_token)
    if not refresh_token or not row.client_id or not row.token_endpoint:
        return None
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": str(row.client_id),
        "resource": server.url,
    }
    client_secret = _maybe_decrypt(secret, row.encrypted_client_secret)
    if client_secret:
        data["client_secret"] = client_secret
    try:
        token = await _post_token(
            row.token_endpoint,
            data,
            transport=transport,
            timeout=settings.mcp_oauth_timeout_seconds,
        )
    except AppError as exc:
        row.last_error = exc.message[:500]
        error_code = str((exc.details or {}).get("error") or "")
        if error_code in ("invalid_grant", "invalid_token", "invalid_client"):
            # refresh 已被撤销/过期:清掉 token,下次调用即 needs_reauth
            row.encrypted_access_token = None
            row.encrypted_refresh_token = None
            row.expires_at = None
        await session.flush()
        return None
    access = _apply_token(row, token, secret)
    row.last_error = None
    await session.flush()
    return access


async def access_token(
    session: AsyncSession,
    tenant_id: str,
    server_id: str,
    *,
    settings: Settings,
    secret: str,
    force_refresh: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """调用前解析 token:未过期直接用;过期/强制则单飞刷新;失败返回 None。"""
    if not settings.mcp_oauth_enabled:
        return None
    repo = MCPServerAuthRepository(session)
    row = await repo.get_for_server(tenant_id, server_id)
    if row is None:
        return None
    token = _maybe_decrypt(secret, row.encrypted_access_token)
    if token and not force_refresh and not _expired(row):
        return token
    if not row.encrypted_refresh_token:
        return None
    server = await MCPServerRepository(session).get_for_tenant(tenant_id, server_id)
    if server is None:
        return None
    async with _lock_for(f"{tenant_id}:{server_id}"):
        # 等锁期间别的调用可能已经刷新过:非强制刷新时重新判断
        if not force_refresh:
            token = _maybe_decrypt(secret, row.encrypted_access_token)
            if token and not _expired(row):
                return token
        return await _refresh(
            session,
            server,
            row,
            secret=secret,
            settings=settings,
            transport=transport,
        )


async def disconnect(session: AsyncSession, tenant_id: str, server_id: str) -> None:
    """解除授权:删掉授权行(不调远端 revoke;token 随行一起消失)。"""
    repo = MCPServerAuthRepository(session)
    row = await repo.get_for_server(tenant_id, server_id)
    if row is not None:
        await repo.delete(row)
