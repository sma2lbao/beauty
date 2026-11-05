"""MCP OAuth 发现、注册与 state 签名(自 mcp_oauth.py 拆出)。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
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


#: 探测结果分类:网络不可达与「有响应但内容不对」必须分开,
#: 否则用户分不清该改网络还是改配置。
_OUTCOME_NETWORK = "network"
_OUTCOME_HTTP = "http"
_OUTCOME_INVALID_JSON = "invalid_json"
_OUTCOME_NOT_OBJECT = "not_object"
_OUTCOME_OK = "ok"


@dataclass(frozen=True)
class _Probe:
    """一次 well-known 探测的结果:保留失败原因,供上层聚合成可区分的错误码。"""

    url: str
    outcome: str
    status: int | None = None
    error: str | None = None
    data: dict[str, Any] | None = None

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"url": self.url, "outcome": self.outcome}
        if self.status is not None:
            detail["status"] = self.status
        if self.error:
            detail["error"] = self.error
        return detail


async def _probe_json(client: httpx.AsyncClient, url: str) -> _Probe:
    """探测一个 well-known 文档;把「网络错 / 非 200 / 非 JSON / 非对象」分别记下来。"""
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return _Probe(url, _OUTCOME_NETWORK, error=f"{type(exc).__name__}: {exc}"[:200])
    if response.status_code != 200:
        return _Probe(url, _OUTCOME_HTTP, status=response.status_code)
    try:
        data = response.json()
    except ValueError:
        return _Probe(url, _OUTCOME_INVALID_JSON, status=response.status_code)
    if not isinstance(data, dict):
        return _Probe(url, _OUTCOME_NOT_OBJECT, status=response.status_code)
    return _Probe(url, _OUTCOME_OK, status=response.status_code, data=data)


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    """只要可用的 JSON 对象(兼容旧调用);新代码一律用 _probe_json 保留失败原因。"""
    probe = await _probe_json(client, url)
    return probe.data if probe.outcome == _OUTCOME_OK else None


async def _probe_all(client: httpx.AsyncClient, urls: Sequence[str]) -> list[_Probe]:
    return [await _probe_json(client, url) for url in urls]


def _has_http_response(probes: Sequence[_Probe]) -> bool:
    """至少有一个候选真的收到了 HTTP 响应 → 说明地址可达,问题在配置/内容。"""
    return any(probe.outcome != _OUTCOME_NETWORK for probe in probes)


def _first_json_probe(probes: Sequence[_Probe]) -> _Probe | None:
    for probe in probes:
        if probe.outcome == _OUTCOME_OK:
            return probe
    return None


def _network_detail(probes: Sequence[_Probe]) -> str:
    """第一条网络错误原文(给用户看的"为什么连不上")。"""
    for probe in probes:
        if probe.error:
            return probe.error
    return "无 HTTP 响应"


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


def _valid_resource_metadata(probes: Sequence[_Probe]) -> dict[str, Any] | None:
    """候选里第一个带 http(s) authorization_servers 的 PRM。"""
    for probe in probes:
        data = probe.data
        if probe.outcome != _OUTCOME_OK or data is None:
            continue
        servers = data.get("authorization_servers")
        if isinstance(servers, list) and any(
            isinstance(item, str) and item.startswith(("http://", "https://"))
            for item in servers
        ):
            return data
    return None


async def probe_resource_challenge(
    client: httpx.AsyncClient, resource_url: str
) -> str | None:
    """主动打一次 MCP initialize:401 的 WWW-Authenticate 里带 resource_metadata。

    预设一键连接没有历史 401 文本(hint 为空),靠这次探测拿到权威的元数据地址,
    而不是只猜 origin 级 well-known;连不通时直接返回 None(不额外拖延失败)。
    """
    payload = {
        "jsonrpc": "2.0",
        "id": "beauty-moon-discovery",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "beauty-moon-agent", "version": "0.1"},
        },
    }
    try:
        response = await client.post(
            resource_url,
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 401:
        return None
    return _challenge_hint(response.headers.get("www-authenticate"))


def _resource_discovery_error(
    resource_url: str, hint: str | None, probes: Sequence[_Probe]
) -> UpstreamError:
    """把探测结果聚合成可区分、可行动的错误(details 带证据链)。"""
    details: dict[str, Any] = {
        "stage": "resource_metadata",
        "resource": resource_url,
        "hint": hint,
        "tried": [probe.as_detail() for probe in probes],
    }
    broken = _first_json_probe(probes)
    if broken is not None:
        return UpstreamError(
            "mcp.oauth_discovery_invalid",
            "MCP server 的 OAuth 资源元数据不合法(缺少 authorization_servers):"
            f"{broken.url}。请检查该 server 的配置;"
            "若它用静态令牌访问,可直接填写令牌连接。",
            details={
                **details,
                "reason": "missing_authorization_servers",
                "action": "use_static_token",
                "url": broken.url,
            },
        )
    if _has_http_response(probes):
        return UpstreamError(
            "mcp.oauth_discovery_failed",
            "该 MCP server 未公布 OAuth 资源元数据(RFC 9728),无法走浏览器授权。"
            "若该 server 用静态令牌/API Key 访问,请改用「更新令牌」;"
            "否则请让 server 维护者开启 OAuth 元数据。",
            details={**details, "reason": "no_metadata", "action": "use_static_token"},
        )
    return UpstreamError(
        "mcp.oauth_discovery_unreachable",
        f"无法连接 MCP server({resource_url}):{_network_detail(probes)}。"
        "请检查服务地址、网络、代理或 DNS 后重试;"
        "若该 server 用静态令牌,也可直接填写令牌连接。",
        details={**details, "reason": "network", "action": "check_network"},
    )


async def discover_protected_resource(
    resource_url: str,
    *,
    hint: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """RFC 9728:找到资源所属的授权服务器(至少一个 http(s) issuer)。

    失败按「网络不可达 / 可达但未公布元数据 / 元数据不合法」返回不同错误码,
    用户据此判断是查网络还是改用静态令牌。
    """
    probes: list[_Probe] = []
    async with _http_client(transport, timeout) as client:
        candidates = _candidate_urls(
            resource_url, suffix=RESOURCE_METADATA_PATH, hint=hint
        )
        probes = await _probe_all(client, candidates)
        data = _valid_resource_metadata(probes)
        if data is not None:
            return data
        # 没有历史 401 文本时主动探测一次 challenge(预设一键连接走这条路径);
        # 完全不可达就别再打 initialize,免得失败延迟翻倍。
        if hint is None and _has_http_response(probes):
            discovered = await probe_resource_challenge(client, resource_url)
            if discovered is not None and discovered not in candidates:
                probe = await _probe_json(client, discovered)
                probes.append(probe)
                data = _valid_resource_metadata([probe])
                if data is not None:
                    return data
    raise _resource_discovery_error(resource_url, hint, probes)


def _authorization_server_error(issuer: str, probes: Sequence[_Probe]) -> UpstreamError:
    """AS 元数据失败同样分「不可达」与「可达但元数据不可用」。"""
    details: dict[str, Any] = {
        "stage": "authorization_server",
        "issuer": issuer,
        "tried": [probe.as_detail() for probe in probes],
    }
    broken = _first_json_probe(probes)
    if broken is not None:
        return UpstreamError(
            "mcp.oauth_authorization_server_invalid",
            f"授权服务器({issuer})的元数据缺少 authorization_endpoint 或 "
            "token_endpoint,无法继续授权。请检查授权服务器配置,或改用静态令牌。",
            details={
                **details,
                "reason": "missing_endpoints",
                "action": "use_static_token",
                "url": broken.url,
            },
        )
    if _has_http_response(probes):
        return UpstreamError(
            "mcp.oauth_authorization_server_invalid",
            f"授权服务器({issuer})未公布可用的 OAuth 元数据(RFC 8414/OIDC)。"
            "请确认授权服务器地址是否正确,或改用静态令牌。",
            details={**details, "reason": "no_metadata", "action": "use_static_token"},
        )
    return UpstreamError(
        "mcp.oauth_authorization_server_unreachable",
        f"无法连接授权服务器({issuer}):{_network_detail(probes)}。"
        "请检查网络与代理后重试,或改用静态令牌。",
        details={**details, "reason": "network", "action": "check_network"},
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
        probes = await _probe_all(client, candidates)
        for probe in probes:
            data = probe.data
            if probe.outcome != _OUTCOME_OK or data is None:
                continue
            if _endpoint(data, "authorization_endpoint") and _endpoint(
                data, "token_endpoint"
            ):
                return data
    raise _authorization_server_error(issuer, probes)


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
    *,
    ok: bool,
    server_name: str | None = None,
    error: str | None = None,
    next_path: str = "/mcp",
) -> str:
    """拼授权后前端落回的相对路径(默认 /mcp,预设连接器页传 /connectors)。"""
    params: dict[str, str] = {"oauth": "success" if ok else "error"}
    if server_name:
        params["server"] = server_name
    if not ok and error:
        params["error"] = error[:300]
    return f"{next_path or '/mcp'}?" + urlencode(params)
