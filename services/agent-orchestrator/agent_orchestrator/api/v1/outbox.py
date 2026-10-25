"""outbox 运维端点(§6.3):失败事件可见、可人工重放。

约定:

- **租户隔离**:只列/只重放调用方租户的事件(平台级运维需要 admin scope,
  阶段 A 不做,故不给跨租户入口);
- **未启用即明确报错**:`OUTBOX_ENABLED=false` 时这些端点返回 409
  `outbox.disabled`,而不是返回空列表——否则运维会把"没开"误读成"没有事件";
- 重放 = 回到 pending + 清零尝试数/退避,由 dispatcher 在下一轮投递;
  重放成功时 `last_error` 才被清空(重放前仍能看到上次失败原因)。

仓储访问收敛在 services/outbox.py(R-29):这里只做参数校验与响应构造。
"""

from typing import Annotated, Any

from common.errors import ConflictError
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_settings, get_tenant_id
from agent_orchestrator.schemas.outbox import (
    OutboxEventRead,
    OutboxStatsRead,
    RequeueResultRead,
)
from agent_orchestrator.services import outbox as outbox_service

router = APIRouter(prefix="/outbox", tags=["outbox"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Any, Depends(get_settings)]


def _require_enabled(settings: Any) -> None:
    if not settings.outbox_enabled:
        raise ConflictError(
            "outbox.disabled",
            "outbox 未启用(OUTBOX_ENABLED=false),没有可运维的事件流",
        )


@router.get("/events", response_model=list[OutboxEventRead])
async def list_outbox_events(
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    status: Annotated[
        str | None, Query(pattern="^(pending|dispatched|failed)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OutboxEventRead]:
    """列出本租户的 outbox 事件(最新在前),可按状态过滤。"""
    _require_enabled(settings)
    events = await outbox_service.list_events(
        session, tenant_id, status=status, limit=limit, offset=offset
    )
    return [OutboxEventRead.model_validate(event) for event in events]


@router.get("/stats", response_model=OutboxStatsRead)
async def outbox_stats(
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> OutboxStatsRead:
    """本租户各状态计数(pending/dispatched/failed)。"""
    _require_enabled(settings)
    counts = await outbox_service.stats(session, tenant_id)
    return OutboxStatsRead(
        pending=counts["pending"],
        dispatched=counts["dispatched"],
        failed=counts["failed"],
    )


@router.post("/events/{event_id}/retry", response_model=OutboxEventRead)
async def retry_outbox_event(
    event_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> OutboxEventRead:
    """人工重放单条事件:回到 pending,等 dispatcher 下一轮投递。"""
    _require_enabled(settings)
    event = await outbox_service.requeue_event(session, tenant_id, event_id)
    return OutboxEventRead.model_validate(event)


@router.post("/retry-failed", response_model=RequeueResultRead)
async def retry_failed_events(
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> RequeueResultRead:
    """批量重放本租户所有 failed 事件(消费者恢复后一键补投)。"""
    _require_enabled(settings)
    requeued = await outbox_service.requeue_failed(session, tenant_id)
    return RequeueResultRead(requeued=requeued)
