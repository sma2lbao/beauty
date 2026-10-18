"""用量与 BYOK 端点(M0-11 本地版)。"""

from __future__ import annotations

import secrets
from typing import Annotated

from common.errors import UnauthorizedError
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, get_settings, get_tenant_id
from account.core.config import Settings
from account.schemas.insights import (
    BYOKRead,
    BYOKUpsert,
    UsageEventCreate,
    UsageSummary,
)
from account.services import insights as insights_service

router = APIRouter(tags=["insights"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/usage/summary", response_model=UsageSummary)
async def usage_summary(
    session: DBSession, tenant_id: TenantId
) -> UsageSummary:
    return await insights_service.usage_summary(session, tenant_id)


@router.post("/usage/events", status_code=status.HTTP_201_CREATED)
async def record_usage(
    body: UsageEventCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    x_internal_secret: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """内部上报(M0 先由 seed/后续 orchestrator 写入)。"""
    expected = settings.internal_secret
    if not expected or not x_internal_secret or not secrets.compare_digest(
        x_internal_secret, expected
    ):
        raise UnauthorizedError("usage.internal_forbidden", "内部上报未开放")
    event = await insights_service.record_usage(session, tenant_id, body)
    return {"id": event.id}


@router.get("/byok", response_model=list[BYOKRead])
async def list_byok(session: DBSession, tenant_id: TenantId) -> list[BYOKRead]:
    return await insights_service.list_byok(session, tenant_id)


@router.put("/byok/{provider}", response_model=BYOKRead)
async def put_byok(
    provider: str,
    body: BYOKUpsert,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> BYOKRead:
    return await insights_service.upsert_byok(
        session, tenant_id, provider, body, secret=settings.byok_secret
    )


@router.delete("/byok/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_byok(
    provider: str, session: DBSession, tenant_id: TenantId
) -> None:
    await insights_service.delete_byok(session, tenant_id, provider)
