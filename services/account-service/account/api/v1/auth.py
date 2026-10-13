"""dev 登录端点(桩):POST /v1/auth/login,声明 email 即签发 access token。

免认证(它是拿 token 的入口);仅 `DEV_AUTH_ENABLED=true` 时挂载。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.errors import UnauthorizedError
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, get_settings
from account.core.config import Settings
from account.core.dev_idp import DevIdP
from account.schemas.auth import (
    DevIssueRequest,
    DevIssueResponse,
    DevLoginRequest,
    DevLoginResponse,
)
from account.services.dev_auth import dev_login

router = APIRouter(prefix="/auth", tags=["auth"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_dev_idp(request: Request) -> DevIdP:
    """create_app 在开桩时装配的签名器(不开桩时该路由不存在)。"""
    return request.app.state.dev_idp


IdpDep = Annotated[DevIdP, Depends(get_dev_idp)]


@router.post("/login", response_model=DevLoginResponse, status_code=status.HTTP_200_OK)
async def login(
    body: DevLoginRequest, session: DBSession, settings: SettingsDep, idp: IdpDep
) -> DevLoginResponse:
    """幂等登录:建档 + 会话登记 + RS256 签发(凭证校验归 IdP,桩不设密码)。"""
    result = await dev_login(session, settings, idp, body)
    return DevLoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        tenant_id=result.organization_id,
        organization_id=result.organization_id,
        user_id=result.user_id,
        subject=result.subject,
        role=result.role,
        session_id=result.session_id,
    )


@router.post("/issue", response_model=DevIssueResponse)
async def issue(
    body: DevIssueRequest, request: Request, settings: SettingsDep, idp: IdpDep
) -> DevIssueResponse:
    """内部服务签发:共享密钥换"以目标租户为 claim"的 access token。

    下游服务开了鉴权后,服务间调用与用户面走**同一套** JwtVerifier——
    不设后门;密钥未配置或不符一律 401(常量时间比较,不泄漏配置状态)。
    """
    expected = settings.dev_internal_secret
    provided = request.headers.get("X-Internal-Secret", "")
    if not expected or not secrets.compare_digest(provided, expected):
        raise UnauthorizedError("auth.internal_forbidden", "内部签发未开放或密钥不符")
    token = idp.sign(subject=body.subject, tenant_id=body.tenant_id)
    return DevIssueResponse(
        access_token=token,
        expires_in=idp.ttl_seconds,
        tenant_id=body.tenant_id,
        subject=body.subject,
    )
