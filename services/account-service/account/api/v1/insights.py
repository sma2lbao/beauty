"""用量与 BYOK 端点(M0-11 本地版)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import (
    get_internal_tenant_id,
    get_session,
    get_settings,
    get_tenant_id,
)
from account.core.config import Settings
from account.schemas.insights import (
    BYOKRead,
    BYOKUpsert,
    UsageEventCreate,
    UsageQuota,
    UsageSummary,
)
from account.services import insights as insights_service

router = APIRouter(tags=["insights"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
InternalTenantId = Annotated[str, Depends(get_internal_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/usage/summary", response_model=UsageSummary)
async def usage_summary(session: DBSession, tenant_id: TenantId) -> UsageSummary:
    return await insights_service.usage_summary(session, tenant_id)


@router.post("/usage/events", status_code=status.HTTP_201_CREATED)
async def record_usage(
    body: UsageEventCreate,
    session: DBSession,
    tenant_id: InternalTenantId,
) -> dict[str, str]:
    """内部上报(orchestrator/P0-1):共享密钥认证,写 usage_events。"""
    event = await insights_service.record_usage(session, tenant_id, body)
    return {"id": event.id}


@router.get("/usage/quota", response_model=UsageQuota)
async def usage_quota(session: DBSession, tenant_id: InternalTenantId) -> UsageQuota:
    """内部读取配额快照(orchestrator/P0-2 建 run 前检查)。"""
    return await insights_service.usage_quota(session, tenant_id)


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
async def delete_byok(provider: str, session: DBSession, tenant_id: TenantId) -> None:
    await insights_service.delete_byok(session, tenant_id, provider)
