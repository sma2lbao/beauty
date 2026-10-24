"""account-service 客户端与 BFF 会话状态。

BFF 只把 access/refresh token 放进加密 Cookie;登录响应、session 响应都
不包含 token 本体。上游错误沿用统一 {code,message,details} 契约。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from common.security import SECRET_HEADER
from common.errors import (
    AppError,
    ConflictError,
    UnauthorizedError,
    UpstreamError,
)

from bff.core.config import Settings
from bff.schemas.auth import SessionUser


def _base(url: str) -> str:
    return url.rstrip("/")


def raise_upstream_error(response: httpx.Response) -> None:
    try:
        body = response.json()
    except ValueError:
        body = {}
    code = body.get("code") or f"bff.upstream_{response.status_code}"
    message = body.get("message") or f"上游服务失败({response.status_code})"
    if response.status_code == 401:
        raise UnauthorizedError(code, message)
    if response.status_code == 409:
        raise ConflictError(code, message)
    if response.status_code >= 500:
        raise UpstreamError(code, message)
    raise AppError(code, message)


async def _account_json(
    client: httpx.AsyncClient,
    settings: Settings,
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = _base(settings.account_base_url) + path
    try:
        response = await client.request(
            method, url, json=json, headers=headers, params=params
        )
    except httpx.HTTPError as exc:
        raise UpstreamError("bff.account_unreachable", "账号服务不可达") from exc
    if response.is_error:
        raise_upstream_error(response)
    if response.status_code == 204 or not response.content:
        return {}
    return response.json()


def session_from_tokens(payload: dict[str, Any]) -> dict[str, Any]:
    """把 account TokenResponse 规范成 BFF Cookie 内容。"""
    expires_in = int(payload.get("expires_in") or 3600)
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat(),
        "user": {
            "user_id": payload["user_id"],
            "tenant_id": payload["tenant_id"],
            "organization_id": payload.get("organization_id", payload["tenant_id"]),
            "subject": payload["subject"],
            "email": payload["subject"],
            "display_name": None,
            "role": payload.get("role", "member"),
            "session_id": payload.get("session_id", ""),
        },
    }


def session_user(state: dict[str, Any]) -> SessionUser:
    return SessionUser.model_validate(state["user"])


def session_expires_at(state: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(state["expires_at"]))


async def login(
    client: httpx.AsyncClient, settings: Settings, *, email: str, password: str
) -> dict[str, Any]:
    payload = await _account_json(
        client,
        settings,
        "POST",
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    return session_from_tokens(payload)


async def register(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    email: str,
    password: str,
    display_name: str | None,
) -> dict[str, Any]:
    payload = await _account_json(
        client,
        settings,
        "POST",
        "/v1/auth/register",
        json={"email": email, "password": password, "display_name": display_name},
    )
    return session_from_tokens(payload)


async def refresh(
    client: httpx.AsyncClient, settings: Settings, state: dict[str, Any]
) -> dict[str, Any]:
    payload = await _account_json(
        client,
        settings,
        "POST",
        "/v1/auth/refresh",
        json={"refresh_token": state["refresh_token"]},
    )
    return session_from_tokens(payload)


async def logout(
    client: httpx.AsyncClient, settings: Settings, state: dict[str, Any]
) -> None:
    """尽力吊销 refresh token;上游失败不阻塞本地 Cookie 清理。"""
    try:
        await _account_json(
            client,
            settings,
            "POST",
            "/v1/auth/logout",
            json={"refresh_token": state["refresh_token"]},
        )
    except AppError:
        return


async def oauth_authorize(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    provider: str,
    redirect_uri: str,
) -> dict[str, Any]:
    return await _account_json(
        client,
        settings,
        "GET",
        f"/v1/auth/oauth/{provider}/authorize",
        params={"redirect_uri": redirect_uri},
    )


async def oauth_exchange(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    provider: str,
    code: str,
    state: str,
) -> dict[str, Any]:
    payload = await _account_json(
        client,
        settings,
        "POST",
        f"/v1/auth/oauth/{provider}/exchange",
        json={"code": code, "state": state},
    )
    return session_from_tokens(payload)


async def issue_stream_token(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    tenant_id: str,
    subject: str,
    run_id: str,
) -> dict[str, Any]:
    if not settings.internal_secret:
        raise AppError(
            "bff.stream_token_disabled",
            "未配置 BFF__INTERNAL_SECRET,stream token 未开放",
        )
    payload = await _account_json(
        client,
        settings,
        "POST",
        "/v1/auth/issue",
        json={"tenant_id": tenant_id, "subject": subject},
        headers={SECRET_HEADER: settings.internal_secret},
    )
    return {
        "token": payload["access_token"],
        "token_type": "Bearer",
        "expires_in": payload["expires_in"],
        "url": _base(settings.runtime_public_url)
        + f"/api/v1/runs/{run_id}/events/stream",
    }
