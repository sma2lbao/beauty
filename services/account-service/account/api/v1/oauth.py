"""社交 OAuth 端点(BFF 调用):授权 URL 与 code 交换。

浏览器不直接访问本端点;BFF 取 authorization_url 后 302,回调用
POST /v1/auth/oauth/{provider}/exchange 换 TokenResponse(仍不落 Cookie)。
"""

from __future__ import annotations

from typing import Annotated

import httpx
from common.errors import AppError
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import account.core.oauth as oauth
from account.api.deps import get_session, get_settings
from account.core.config import Settings
from account.core.idp import IdPSigner
from account.schemas.auth import TokenResponse
from account.services import auth_service

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
DBSession = Annotated[AsyncSession, Depends(get_session)]


def get_signer(request: Request) -> IdPSigner:
    return request.app.state.signer


SignerDep = Annotated[IdPSigner, Depends(get_signer)]


class OAuthAuthorizeResponse(BaseModel):
    authorization_url: str
    state: str


class OAuthExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=4096)


def _oauth_client(request: Request) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=request.app.state.oauth_transport)


@router.get("/{provider}/authorize", response_model=OAuthAuthorizeResponse)
async def authorize(
    provider: str,
    request: Request,
    redirect_uri: Annotated[str, Query(min_length=8, max_length=2048)],
    settings: SettingsDep,
) -> OAuthAuthorizeResponse:
    if not redirect_uri.startswith(("http://", "https://")):
        raise AppError("oauth.invalid_redirect", "redirect_uri 非法")
    url, state = oauth.authorize_url(settings, provider, redirect_uri)
    return OAuthAuthorizeResponse(authorization_url=url, state=state)


@router.post("/{provider}/exchange", response_model=TokenResponse)
async def exchange(
    provider: str,
    body: OAuthExchangeRequest,
    request: Request,
    session: DBSession,
    settings: SettingsDep,
    signer: SignerDep,
) -> TokenResponse:
    async with _oauth_client(request) as client:
        profile = await oauth.exchange(
            client, settings, provider, body.code, body.state
        )
    result = await auth_service.oauth_login(session, settings, signer, profile)
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=result.expires_in,
        tenant_id=result.tenant_id,
        organization_id=result.organization_id,
        user_id=result.user_id,
        subject=result.subject,
        role=result.role,
        session_id=result.session_id,
    )
