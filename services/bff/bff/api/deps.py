"""FastAPI 依赖:配置、上游 client、Cookie 会话与 CSRF。"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

import httpx
from common.errors import PermissionDeniedError, UnauthorizedError
from fastapi import Depends, Header, Request

from bff.core.config import Settings
from bff.core.security import SessionCodec
from bff.schemas.auth import SessionUser


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.upstream_client


def get_codec(request: Request) -> SessionCodec:
    return request.app.state.session_codec


def get_session_state(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    codec: SessionCodec = request.app.state.session_codec
    state = codec.decode(request.cookies.get(settings.cookie_name))
    if state is None or not state.get("access_token"):
        raise UnauthorizedError("auth.session_missing", "登录态不存在或已失效")
    return state


def get_proxy_auth(request: Request) -> dict[str, Any]:
    """代理鉴权:session Cookie 优先;token 模式开发兜底用 Bearer 直通。"""
    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return {
            "access_token": authorization.split(" ", 1)[1].strip(),
            "refresh_token": None,
            "user": {},
            "bearer": True,
        }
    state = get_session_state(request)
    return {**state, "bearer": False}


def get_session_user(
    state: Annotated[dict[str, Any], Depends(get_session_state)],
) -> SessionUser:
    return SessionUser.model_validate(state["user"])


def require_csrf(
    request: Request,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """double-submit:非 HttpOnly cookie 与请求头一致才放行。"""
    settings: Settings = request.app.state.settings
    cookie = request.cookies.get(settings.csrf_cookie_name)
    if not cookie or not x_csrf_token or not hmac.compare_digest(cookie, x_csrf_token):
        raise PermissionDeniedError("auth.csrf_invalid", "CSRF 校验失败,请刷新页面")
