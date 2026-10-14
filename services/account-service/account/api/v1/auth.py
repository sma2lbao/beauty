"""认证端点(自建):注册 / 登录 / 刷新 / 登出 / 内部签发。

注册与登录免认证(它们是拿 token 的入口);刷新/登出按 refresh token
本身鉴权(哈希查库 + 吊销语义,见 services/auth_service.py)。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.errors import UnauthorizedError
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, get_settings
from account.core.config import Settings
from account.core.idp import IdPSigner
from account.schemas.auth import (
    IssueRequest,
    IssueResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from account.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_signer(request: Request) -> IdPSigner:
    """create_app 装配的签发器(自建认证始终挂载)。"""
    return request.app.state.signer


SignerDep = Annotated[IdPSigner, Depends(get_signer)]


@router.post(
    "/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    body: RegisterRequest, session: DBSession, settings: SettingsDep, signer: SignerDep
) -> TokenResponse:
    """注册:新组织 + owner 成员(409 = 邮箱已被注册)。"""
    result = await auth_service.register(session, settings, signer, body)
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


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, session: DBSession, settings: SettingsDep, signer: SignerDep
) -> TokenResponse:
    """登录:邮箱 + 密码换双令牌(失败统一 401,不区分用户不存在与密码错)。"""
    result = await auth_service.login(session, settings, signer, body)
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


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    session: DBSession,
    settings: SettingsDep,
    signer: SignerDep,
) -> TokenResponse:
    """旋转式刷新:旧 refresh token 换新令牌对(旧的一次性作废)。"""
    result = await auth_service.refresh_tokens(
        session, settings, signer, body.refresh_token
    )
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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: LogoutRequest, session: DBSession) -> Response:
    """登出:吊销 refresh token;幂等(未知令牌也 204)。"""
    await auth_service.logout(session, body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/issue", response_model=IssueResponse)
async def issue(
    body: IssueRequest, request: Request, settings: SettingsDep, signer: SignerDep
) -> IssueResponse:
    """内部服务签发:共享密钥换"以目标租户为 claim"的 access token。

    下游服务开了鉴权后,服务间调用与用户面走**同一套** JwtVerifier——
    不设后门;密钥未配置或不符一律 401(常量时间比较,不泄漏配置状态)。
    """
    expected = settings.internal_secret
    provided = request.headers.get("X-Internal-Secret", "")
    if not expected or not secrets.compare_digest(provided, expected):
        raise UnauthorizedError("auth.internal_forbidden", "内部签发未开放或密钥不符")
    token = signer.sign(subject=body.subject, tenant_id=body.tenant_id)
    return IssueResponse(
        access_token=token,
        expires_in=signer.ttl_seconds,
        tenant_id=body.tenant_id,
        subject=body.subject,
    )
