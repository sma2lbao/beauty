"""OAuth 2.1 端点(M3-04):注册、授权确认、token、introspect。"""

from __future__ import annotations

import secrets
from typing import Annotated, Any
from urllib.parse import parse_qs

from common.errors import AppError, UnauthorizedError
from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, get_settings, get_tenant_id
from account.core.config import Settings
from account.schemas.oauth21 import (
    AuthorizeApprove,
    AuthorizedClientRead,
    AuthorizeResult,
    OAuthClientRead,
    OAuthClientRegister,
    OAuthIntrospectRead,
    TokenResponse,
)
from account.services import oauth21 as oauth_service

router = APIRouter(prefix="/oauth", tags=["oauth21"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _oauth_error(error: str, description: str, code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description}, status_code=code
    )


async def _request_params(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        raw = (await request.body()).decode()
        parsed = parse_qs(raw, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}
    if "application/json" in content_type:
        body = await request.json()
        return {key: str(value) for key, value in body.items() if value is not None}
    return {}


@router.post("/register", response_model=OAuthClientRead, status_code=201)
async def register_client(
    body: OAuthClientRegister, request: Request, session: DBSession
) -> OAuthClientRead | JSONResponse:
    limiter = request.app.state.oauth_register_limiter
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(f"oauth-register:{client_ip}"):
        return JSONResponse(
            {
                "error": "temporarily_unavailable",
                "error_description": "动态注册过于频繁,请稍后重试",
            },
            status_code=429,
        )
    return await oauth_service.register_client(session, body)


@router.get("/clients/{client_id}", response_model=OAuthClientRead)
async def get_client(client_id: str, session: DBSession) -> OAuthClientRead:
    """授权页展示客户端信息;不含 secret(公开客户端)。"""
    return await oauth_service.get_client(session, client_id)


@router.post("/authorize/approve", response_model=AuthorizeResult)
async def approve_authorization(
    body: AuthorizeApprove,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> AuthorizeResult:
    return await oauth_service.authorize(session, tenant_id, body, settings)


@router.post("/token", response_model=TokenResponse)
async def token(request: Request, session: DBSession, settings: SettingsDep):
    params = await _request_params(request)
    grant_type = params.get("grant_type", "")
    client_id = params.get("client_id", "")
    try:
        if grant_type == "authorization_code":
            return await oauth_service.exchange_code(
                session,
                code=params.get("code", ""),
                client_id=client_id,
                redirect_uri=params.get("redirect_uri", ""),
                code_verifier=params.get("code_verifier", ""),
                settings=settings,
            )
        if grant_type == "refresh_token":
            return await oauth_service.refresh_tokens(
                session,
                refresh_token=params.get("refresh_token", ""),
                client_id=client_id,
                settings=settings,
            )
    except AppError as exc:
        return _oauth_error("invalid_grant", exc.message)
    return _oauth_error("unsupported_grant_type", f"不支持的 grant_type:{grant_type}")


@router.post("/introspect", response_model=OAuthIntrospectRead)
async def introspect(
    request: Request,
    session: DBSession,
    settings: SettingsDep,
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> OAuthIntrospectRead | JSONResponse:
    expected = settings.internal_secret
    if not expected or not x_internal_secret or not secrets.compare_digest(
        x_internal_secret, expected
    ):
        raise UnauthorizedError("oauth.internal_forbidden", "内部 introspect 未开放")
    body: dict[str, Any] = await request.json()
    return await oauth_service.introspect(session, str(body.get("token") or ""))


@router.post("/revoke")
async def revoke(request: Request, session: DBSession) -> dict[str, bool]:
    """RFC 7009 token 撤销;未知 token 也 200(不泄漏存在性)。"""
    params = await _request_params(request)
    await oauth_service.revoke_token(
        session, params.get("token", ""), params.get("client_id")
    )
    return {"ok": True}


@router.get("/authorizations", response_model=list[AuthorizedClientRead])
async def list_authorizations(
    session: DBSession, tenant_id: TenantId
) -> list[AuthorizedClientRead]:
    return await oauth_service.list_authorizations(session, tenant_id)


@router.delete(
    "/authorizations/{client_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_authorization(
    client_id: str, session: DBSession, tenant_id: TenantId
) -> Response:
    await oauth_service.revoke_authorization(session, tenant_id, client_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
