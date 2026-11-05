"""连接器目录、授权与执行端点(设计 §5.3)。

公开面(前端经 api-gateway 调):
- GET  /v1/connectors                     目录 + 当前连接状态
- POST /v1/connectors/{provider}/authorize 发起用户授权(返回授权页 URL)
- GET  /v1/oauth/{provider}/callback        provider 回调,302 回前端

内部面(tool-service 调,X-Internal-Secret + X-Account-Id):
- GET  /v1/internal/connectors/tools       该账号可用工具 descriptor
- POST /v1/connectors/{provider}/execute   按账号解析连接并执行工具
"""

from __future__ import annotations

import httpx
from common.service_kit import auth_dependencies
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from connector.api.deps import (
    get_account_id,
    get_internal_account_id,
    get_owner_subject,
    get_session,
)
from connector.core.config import Settings
from connector.core.http import get_http_transport
from connector.schemas.connectors import (
    AuthorizeResponse,
    ConnectorsRead,
    ExecuteRequest,
)
from connector.services import catalog as catalog_service
from connector.services import execute as execute_service
from connector.services import oauth as oauth_service

router = APIRouter(tags=["connectors"])

DBSession = Depends(get_session)
AccountId = Depends(get_account_id)
OwnerSubject = Depends(get_owner_subject)
InternalAuth = auth_dependencies(
    error_code="connector.internal_forbidden",
    error_message="内部调用未开放",
)
InternalAccount = Depends(get_internal_account_id)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _transport(request: Request) -> httpx.AsyncBaseTransport | None:
    return get_http_transport(request)


@router.get("/connectors", response_model=ConnectorsRead)
async def list_connectors(
    request: Request,
    session: AsyncSession = DBSession,
    account_id: str = AccountId,
) -> ConnectorsRead:
    return await catalog_service.list_providers(session, _settings(request), account_id)


@router.post("/connectors/{provider}/authorize", response_model=AuthorizeResponse)
async def authorize(
    provider: str,
    request: Request,
    session: AsyncSession = DBSession,
    account_id: str = AccountId,
    owner_subject: str | None = OwnerSubject,
) -> AuthorizeResponse:
    start = await oauth_service.start_authorize(
        session,
        _settings(request),
        account_id=account_id,
        provider_name=provider,
        owner_subject=owner_subject,
    )
    return AuthorizeResponse(
        provider=provider,
        authorize_url=start.authorize_url,
        expires_at=start.expires_at,
    )


@router.get("/oauth/{provider}/callback", response_model=None)
async def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = DBSession,
) -> RedirectResponse:
    """provider 回调:不返回 token,只 302 回前端并带状态结果。"""
    result = await oauth_service.handle_callback(
        session,
        _settings(request),
        provider,
        code=code,
        state=state,
        error=error,
        transport=_transport(request),
    )
    target = oauth_service.success_redirect(_settings(request), result)
    return RedirectResponse(target, status_code=302)


@router.post("/connectors/{provider}/execute")
async def execute(
    provider: str,
    payload: ExecuteRequest,
    request: Request,
    _internal: InternalAuth,
    session: AsyncSession = DBSession,
    account_id: str = InternalAccount,
) -> dict[str, object]:
    return await execute_service.execute_tool(
        session,
        _settings(request),
        account_id=account_id,
        provider_name=provider,
        tool_name=payload.tool,
        arguments=payload.arguments,
        transport=_transport(request),
        run_id=payload.run_id,
    )


@router.get("/internal/connectors/tools")
async def internal_tools(
    request: Request,
    _internal: InternalAuth,
    session: AsyncSession = DBSession,
    account_id: str = InternalAccount,
) -> dict[str, object]:
    return await catalog_service.tool_descriptor_payloads(
        session, _settings(request), account_id
    )
