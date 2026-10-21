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
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

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
from agent_orchestrator.services.mcp_crypto import decrypt_token, encrypt_token

RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
AS_METADATA_PATH = "/.well-known/oauth-authorization-server"
OIDC_CONFIGURATION_PATH = "/.well-known/openid-configuration"

#: 过期前多少秒就提前刷新,避免"刚好在调用途中过期"
EXPIRY_SKEW_SECONDS = 60

_USER_AGENT = "beauty-moon-agent/0.1"

#: 同一 server 的刷新单飞:并发调用只让一个请求去换 token
_refresh_locks: dict[str, asyncio.Lock] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signing_secret(settings: Settings) -> str:
    return settings.mcp_oauth_state_secret or settings.mcp_secret


def _http_client(
    transport: httpx.AsyncBaseTransport | None, timeout: float
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
        transport=transport,
        follow_redirects=True,
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
    )


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    """GET 一个 well-known 文档;不可达/非 200/非 JSON 都返回 None 继续下一个候选。"""
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _candidate_urls(
    base_url: str, *, suffix: str, hint: str | None = None
) -> list[str]:
    """well-known 候选:显式 hint → path 变体 → origin 根变体(RFC 9728/8414)。"""
    urls: list[str] = []
    if hint and hint.startswith(("http://", "https://")):
        urls.append(hint)
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        return urls
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    if path:
        urls.append(f"{origin}{suffix}{path}")
    urls.append(f"{origin}{suffix}")
    # 去重但保持顺序
    unique: list[str] = []
    for url in urls:
        if url not in unique:
            unique.append(url)
    return unique


def _endpoint(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _challenge_hint(last_error: str | None) -> str | None:
    """从 401 的 WWW-Authenticate 原文里捞 resource_metadata 提示(P0 落库的文本)。"""
    if not last_error:
        return None
    marker = "resource_metadata="
    index = last_error.find(marker)
    if index < 0:
        return None
    raw = last_error[index + len(marker) :].split()[0].strip('",')
    return raw if raw.startswith(("http://", "https://")) else None


async def discover_protected_resource(
    resource_url: str,
    *,
    hint: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """RFC 9728:找到资源所属的授权服务器(至少一个 http(s) issuer)。"""
    async with _http_client(transport, timeout) as client:
        for url in _candidate_urls(
            resource_url, suffix=RESOURCE_METADATA_PATH, hint=hint
        ):
            data = await _get_json(client, url)
            if data is None:
                continue
            servers = data.get("authorization_servers")
            if isinstance(servers, list) and any(
                isinstance(item, str) and item.startswith(("http://", "https://"))
                for item in servers
            ):
                return data
    raise UpstreamError(
        "mcp.oauth_discovery_failed",
        "该 MCP server 未公布 OAuth 资源元数据(RFC 9728),无法发起浏览器授权",
    )


async def discover_authorization_server(
    issuer: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """RFC 8414 优先、OIDC 发现兜底;必须拿到 authorization/token 两个端点。"""
    candidates = _candidate_urls(issuer, suffix=AS_METADATA_PATH)
    candidates += _candidate_urls(issuer, suffix=OIDC_CONFIGURATION_PATH)
    async with _http_client(transport, timeout) as client:
        for url in candidates:
            data = await _get_json(client, url)
            if data is None:
                continue
            if _endpoint(data, "authorization_endpoint") and _endpoint(
                data, "token_endpoint"
            ):
                return data
    raise UpstreamError(
        "mcp.oauth_discovery_failed",
        "授权服务器的元数据不可用(缺少 authorization/token 端点)",
        details={"issuer": issuer},
    )


async def register_client(
    metadata: dict[str, Any],
    redirect_uri: str,
    *,
    client_name: str,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """RFC 7591 动态注册:拿 client_id(部分服务器还会发 client_secret)。"""
    endpoint = _endpoint(metadata, "registration_endpoint")
    if endpoint is None:
        raise UpstreamError(
            "mcp.oauth_registration_unsupported",
            "授权服务器不支持动态客户端注册(RFC 7591),无法自动建立浏览器授权",
        )
    payload = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    async with _http_client(transport, timeout) as client:
        try:
            response = await client.post(endpoint, json=payload)
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "mcp.oauth_registration_failed", "动态客户端注册请求失败"
            ) from exc
    if response.status_code not in (200, 201):
        raise UpstreamError(
            "mcp.oauth_registration_failed",
            f"动态客户端注册失败(HTTP {response.status_code})",
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise UpstreamError(
            "mcp.oauth_registration_failed", "动态客户端注册返回了非 JSON 响应"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("client_id"), str):
        raise UpstreamError(
            "mcp.oauth_registration_failed", "动态客户端注册响应缺少 client_id"
        )
    return data


def _new_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _b64e(digest)


def sign_state(settings: Settings, payload: dict[str, Any]) -> str:
    """HMAC-SHA256 签名的 state:payload 自带 ts/nonce,服务端无状态校验。"""
    body = _b64e(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signature = hmac.new(
        _signing_secret(settings).encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_b64e(signature)}"


def verify_state(settings: Settings, value: str) -> dict[str, Any]:
    body, _, signature = (value or "").partition(".")
    if not body or not signature:
        raise AppError("mcp.oauth_state_invalid", "授权 state 不合法，请重新发起授权")
    expected = hmac.new(
        _signing_secret(settings).encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        given = _b64d(signature)
    except ValueError as exc:
        raise AppError(
            "mcp.oauth_state_invalid", "授权 state 不合法，请重新发起授权"
        ) from exc
    if not hmac.compare_digest(given, expected):
        raise AppError(
            "mcp.oauth_state_invalid", "授权 state 签名校验失败，请重新发起授权"
        )
    try:
        payload = json.loads(_b64d(body))
    except ValueError as exc:
        raise AppError(
            "mcp.oauth_state_invalid", "授权 state 不合法，请重新发起授权"
        ) from exc
    if not isinstance(payload, dict):
        raise AppError("mcp.oauth_state_invalid", "授权 state 不合法，请重新发起授权")
    timestamp = payload.get("ts")
    if not isinstance(timestamp, (int, float)) or (
        timestamp + settings.mcp_oauth_state_ttl_seconds < time.time()
    ):
        raise AppError(
            "mcp.oauth_state_expired",
            "授权链接已过期，请回到 MCP 页面重新发起授权",
        )
    return payload


def _maybe_decrypt(secret: str, value: str | None) -> str | None:
    if not value:
        return None
    try:
        return decrypt_token(secret, value)
    except AppError:
        return None


def redirect_path(
    *, ok: bool, server_name: str | None = None, error: str | None = None
) -> str:
    params: dict[str, str] = {"oauth": "success" if ok else "error"}
    if server_name:
        params["server"] = server_name
    if not ok and error:
        params["error"] = error[:300]
    return "/mcp?" + urlencode(params)


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
