"""MCP OAuth 发现、注册与 state 签名(自 mcp_oauth.py 拆出)。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from common.errors import AppError, UpstreamError

from agent_orchestrator.core.config import Settings
from agent_orchestrator.services.mcp_crypto import decrypt_token

RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"


AS_METADATA_PATH = "/.well-known/oauth-authorization-server"


OIDC_CONFIGURATION_PATH = "/.well-known/openid-configuration"


EXPIRY_SKEW_SECONDS = 60


_USER_AGENT = "beauty-moon-agent/0.1"


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
