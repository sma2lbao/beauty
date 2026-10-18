"""BFF 认证端点:邮箱密码 / 会话 / 刷新 / 登出。

双令牌只进 httpOnly 加密 Cookie;响应体只给 SPA 用户信息。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import RedirectResponse

from bff.api.deps import (
    get_client,
    get_codec,
    get_session_state,
    get_settings,
    require_csrf,
)
from bff.core.config import Settings
from bff.core.security import SessionCodec, new_csrf_token
from bff.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    SessionRead,
    SessionUser,
)
from bff.services import account as account_service


def _login_error(settings: Settings, code: str) -> RedirectResponse:
    query = urlencode({"oauth_error": code})
    return RedirectResponse(
        f"{settings.app_origin.rstrip('/')}/login?{query}", status_code=302
    )

router = APIRouter(prefix="/api/auth", tags=["auth"])


def set_session_cookies(
    response: Response,
    settings: Settings,
    codec: SessionCodec,
    state: dict[str, Any],
) -> None:
    response.set_cookie(
        settings.cookie_name,
        codec.encode(state),
        max_age=settings.cookie_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        new_csrf_token(),
        max_age=settings.cookie_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _session_read(state: dict[str, Any]) -> SessionRead:
    return SessionRead(
        user=SessionUser.model_validate(state["user"]),
        expires_at=datetime.fromisoformat(str(state["expires_at"])),
    )


@router.post("/login", response_model=SessionRead)
async def login(
    body: LoginRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
) -> SessionRead:
    state = await account_service.login(
        client, settings, email=body.email, password=body.password
    )
    set_session_cookies(response, settings, codec, state)
    return _session_read(state)


@router.post("/register", response_model=SessionRead, status_code=201)
async def register(
    body: RegisterRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
) -> SessionRead:
    state = await account_service.register(
        client,
        settings,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
    )
    set_session_cookies(response, settings, codec, state)
    return _session_read(state)


@router.post("/refresh", response_model=SessionRead)
async def refresh(
    response: Response,
    state: Annotated[dict[str, object], Depends(get_session_state)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> SessionRead:
    try:
        fresh = await account_service.refresh(client, settings, state)
    except Exception:
        clear_session_cookies(response, settings)
        raise
    set_session_cookies(response, settings, codec, fresh)
    return _session_read(fresh)


@router.get("/session", response_model=SessionRead)
async def session(
    response: Response,
    state: Annotated[dict[str, object], Depends(get_session_state)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
) -> SessionRead:
    expires = datetime.fromisoformat(str(state["expires_at"]))
    if expires <= datetime.now(UTC):
        try:
            state = await account_service.refresh(client, settings, state)
        except Exception:
            clear_session_cookies(response, settings)
            raise
        set_session_cookies(response, settings, codec, state)
    return _session_read(state)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    state: Annotated[dict[str, object], Depends(get_session_state)],
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> None:
    await account_service.logout(client, settings, state)
    clear_session_cookies(response, settings)


@router.get("/oauth/{provider}/start")
async def oauth_start(
    provider: str,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
) -> RedirectResponse:
    redirect_uri = (
        f"{settings.app_origin.rstrip('/')}/api/auth/oauth/{provider}/callback"
    )
    data = await account_service.oauth_authorize(
        client, settings, provider=provider, redirect_uri=redirect_uri
    )
    return RedirectResponse(data["authorization_url"], status_code=302)


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_client)],
    codec: Annotated[SessionCodec, Depends(get_codec)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    state: Annotated[str | None, Query(max_length=4096)] = None,
) -> RedirectResponse:
    if not code or not state:
        return _login_error(settings, "oauth_cancelled")
    try:
        fresh = await account_service.oauth_exchange(
            client, settings, provider=provider, code=code, state=state
        )
    except Exception as exc:  # noqa: BLE001 - 登录失败统一回登录页
        code_name = getattr(exc, "code", "oauth_failed")
        return _login_error(settings, str(code_name))
    redirect = RedirectResponse(
        f"{settings.app_origin.rstrip('/')}/", status_code=302
    )
    set_session_cookies(redirect, settings, codec, fresh)
    return redirect
