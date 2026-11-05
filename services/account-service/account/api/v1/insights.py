"""用量与 BYOK 端点(M0-11 本地版;BYOK 信封 v2 见 core.byok_kms)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import (
    get_account_id,
    get_byok_kms,
    get_internal_account_id,
    get_session,
)
from account.core.byok_kms import BYOKKms
from account.schemas.insights import (
    BYOKCatalogRead,
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
AccountId = Annotated[str, Depends(get_account_id)]
InternalAccountId = Annotated[str, Depends(get_internal_account_id)]
Kms = Annotated[BYOKKms, Depends(get_byok_kms)]


@router.get("/usage/summary", response_model=UsageSummary)
async def usage_summary(session: DBSession, account_id: AccountId) -> UsageSummary:
    return await insights_service.usage_summary(session, account_id)


@router.post("/usage/events", status_code=status.HTTP_201_CREATED)
async def record_usage(
    body: UsageEventCreate,
    session: DBSession,
    account_id: InternalAccountId,
) -> dict[str, str]:
    """内部上报(orchestrator/P0-1):共享密钥认证,写 usage_events。"""
    event = await insights_service.record_usage(session, account_id, body)
    return {"id": event.id}


@router.get("/usage/quota", response_model=UsageQuota)
async def usage_quota(session: DBSession, account_id: InternalAccountId) -> UsageQuota:
    """内部读取配额快照(orchestrator/P0-2 建 run 前检查)。"""
    return await insights_service.usage_quota(session, account_id)


@router.get("/byok", response_model=list[BYOKRead])
async def list_byok(session: DBSession, account_id: AccountId) -> list[BYOKRead]:
    return await insights_service.list_byok(session, account_id)


@router.get("/byok/catalog", response_model=BYOKCatalogRead)
async def byok_catalog(
    response: Response,
    session: DBSession,
    account_id: InternalAccountId,
) -> BYOKCatalogRead:
    """内部读取账号 BYOK 自定义模型并集(网关白名单/目录):响应禁止缓存。

    必须声明在 /byok/{provider} 之前,避免被通配路由抢占。
    """
    response.headers["Cache-Control"] = "no-store"
    return await insights_service.custom_models(session, account_id)


@router.put("/byok/{provider}", response_model=BYOKRead)
async def put_byok(
    provider: str,
    body: BYOKUpsert,
    session: DBSession,
    account_id: AccountId,
    kms: Kms,
) -> BYOKRead:
    return await insights_service.upsert_byok(
        session, account_id, provider, body, kms=kms
    )


@router.delete("/byok/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_byok(provider: str, session: DBSession, account_id: AccountId) -> None:
    await insights_service.delete_byok(session, account_id, provider)


@router.get("/byok/{provider}/secret", response_model=BYOKSecretRead)
async def get_byok_secret(
    provider: str,
    response: Response,
    session: DBSession,
    account_id: InternalAccountId,
    kms: Kms,
) -> BYOKSecretRead:
    """内部解析明文 BYOK(仅服务间,llm-gateway 取用):响应禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"
    return await insights_service.get_byok_secret(
        session, account_id, provider, kms=kms
    )


@router.post("/byok/rotate", response_model=BYOKRotateResult)
async def rotate_byok(
    session: DBSession, account_id: InternalAccountId, kms: Kms
) -> BYOKRotateResult:
    """内部批量轮换:把账号全部 BYOK 密文重加密到当前 KMS key(运维触发)。"""
    return await insights_service.rotate_byok(session, account_id, kms=kms)
