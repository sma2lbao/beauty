"""API Key 端点(M3-01):用户面 CRUD + 内部校验。"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.errors import UnauthorizedError
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, get_settings, get_tenant_id
from account.core.config import Settings
from account.schemas.api_keys import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ApiKeyVerifyRead,
    ApiKeyVerifyRequest,
)
from account.services import api_keys as api_keys_service

router = APIRouter(tags=["api-keys"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    session: DBSession, tenant_id: TenantId
) -> list[ApiKeyRead]:
    return await api_keys_service.list_keys(session, tenant_id)


@router.post(
    "/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED
)
async def create_api_key(
    body: ApiKeyCreate, session: DBSession, tenant_id: TenantId
) -> ApiKeyCreated:
    return await api_keys_service.create_key(session, tenant_id, body)


@router.post("/api-keys/{key_id}/rotate", response_model=ApiKeyCreated)
async def rotate_api_key(
    key_id: str, session: DBSession, tenant_id: TenantId
) -> ApiKeyCreated:
    return await api_keys_service.rotate_key(session, tenant_id, key_id)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str, session: DBSession, tenant_id: TenantId
) -> None:
    await api_keys_service.revoke_key(session, tenant_id, key_id)


@router.post("/api-keys/verify", response_model=ApiKeyVerifyRead)
async def verify_api_key(
    body: ApiKeyVerifyRequest,
    session: DBSession,
    settings: SettingsDep,
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> ApiKeyVerifyRead:
    """内部校验(MCP Server/公开 API 用);共享密钥保护。"""
    expected = settings.internal_secret
    if not expected or not x_internal_secret or not secrets.compare_digest(
        x_internal_secret, expected
    ):
        raise UnauthorizedError("api_key.internal_forbidden", "内部校验未开放")
    return await api_keys_service.verify_key(session, body.key)
