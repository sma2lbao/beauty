"""用量与 BYOK 端点(M0-11 本地版;BYOK 信封 v2 见 core.byok_kms)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import (
    get_byok_kms,
    get_internal_tenant_id,
    get_session,
    get_tenant_id,
)
from account.core.byok_kms import BYOKKms
from account.schemas.insights import (
    BYOKRead,
    BYOKRotateResult,
    BYOKSecretRead,
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
Kms = Annotated[BYOKKms, Depends(get_byok_kms)]


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
    kms: Kms,
) -> BYOKRead:
    return await insights_service.upsert_byok(
        session, tenant_id, provider, body, kms=kms
    )


@router.delete("/byok/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_byok(provider: str, session: DBSession, tenant_id: TenantId) -> None:
    await insights_service.delete_byok(session, tenant_id, provider)


@router.get("/byok/{provider}/secret", response_model=BYOKSecretRead)
async def get_byok_secret(
    provider: str,
    response: Response,
    session: DBSession,
    tenant_id: InternalTenantId,
    kms: Kms,
) -> BYOKSecretRead:
    """内部解析明文 BYOK(仅服务间,llm-gateway 取用):响应禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"
    return await insights_service.get_byok_secret(session, tenant_id, provider, kms=kms)


@router.post("/byok/rotate", response_model=BYOKRotateResult)
async def rotate_byok(
    session: DBSession, tenant_id: InternalTenantId, kms: Kms
) -> BYOKRotateResult:
    """内部批量轮换:把租户全部 BYOK 密文重加密到当前 KMS key(运维触发)。"""
    return await insights_service.rotate_byok(session, tenant_id, kms=kms)
