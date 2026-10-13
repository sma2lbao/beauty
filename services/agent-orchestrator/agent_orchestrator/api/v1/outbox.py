"""outbox 运维端点(§6.3):失败事件可见、可人工重放。

约定:

- **租户隔离**:只列/只重放调用方租户的事件(平台级运维需要 admin scope,
  阶段 A 不做,故不给跨租户入口);
- **未启用即明确报错**:`OUTBOX_ENABLED=false` 时这些端点返回 409
  `outbox.disabled`,而不是返回空列表——否则运维会把"没开"误读成"没有事件";
- 重放 = 回到 pending + 清零尝试数/退避,由 dispatcher 在下一轮投递;
  重放成功时 `last_error` 才被清空(重放前仍能看到上次失败原因)。
"""

from __future__ import annotations

from typing import Annotated

from common.errors import ConflictError, NotFoundError
from events.outbox import DISPATCHED, FAILED, PENDING, OutboxRepository
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_tenant_id
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.outbox import (
    OutboxEventRead,
    OutboxStatsRead,
    RequeueResultRead,
)

router = APIRouter(prefix="/outbox", tags=["outbox"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]


def _settings_of(request: Request) -> Settings:
    """与 runs 端点一致:设置从 app.state 读(本服务没有 get_settings 依赖)。"""
    settings: Settings = request.app.state.settings
    return settings


def _require_enabled(settings: Settings) -> None:
    if not settings.outbox_enabled:
        raise ConflictError(
            "outbox.disabled",
            "outbox 未启用(OUTBOX_ENABLED=false),没有可运维的事件流",
        )


@router.get("/events", response_model=list[OutboxEventRead])
async def list_outbox_events(
    request: Request,
    session: DBSession,
    tenant_id: TenantId,
    status: Annotated[
        str | None, Query(pattern="^(pending|dispatched|failed)$")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OutboxEventRead]:
    """列出本租户的 outbox 事件(最新在前),可按状态过滤。"""
    _require_enabled(_settings_of(request))
    events = await OutboxRepository(session).list_events(
        tenant_id=tenant_id, status=status, limit=limit, offset=offset
    )
    return [OutboxEventRead.model_validate(event) for event in events]


@router.get("/stats", response_model=OutboxStatsRead)
async def outbox_stats(
    request: Request, session: DBSession, tenant_id: TenantId
) -> OutboxStatsRead:
    """本租户各状态计数(pending/dispatched/failed)。"""
    _require_enabled(_settings_of(request))
    counts = await OutboxRepository(session).stats(tenant_id=tenant_id)
    return OutboxStatsRead(
        pending=counts.get(PENDING, 0),
        dispatched=counts.get(DISPATCHED, 0),
        failed=counts.get(FAILED, 0),
    )


@router.post("/events/{event_id}/retry", response_model=OutboxEventRead)
async def retry_outbox_event(
    event_id: str,
    request: Request,
    session: DBSession,
    tenant_id: TenantId,
) -> OutboxEventRead:
    """人工重放单条事件:回到 pending,等 dispatcher 下一轮投递。"""
    _require_enabled(_settings_of(request))
    repository = OutboxRepository(session)
    event = await repository.get_by_id(event_id)
    # 跨租户一律 404(不泄漏存在性),与其它资源同一约定
    if event is None or event.tenant_id != tenant_id:
        raise NotFoundError(
            "outbox.event_not_found",
            "outbox 事件不存在",
            details={"event_id": event_id},
        )
    await repository.requeue(event)
    return OutboxEventRead.model_validate(event)


@router.post("/retry-failed", response_model=RequeueResultRead)
async def retry_failed_events(
    request: Request, session: DBSession, tenant_id: TenantId
) -> RequeueResultRead:
    """批量重放本租户所有 failed 事件(消费者恢复后一键补投)。"""
    _require_enabled(_settings_of(request))
    requeued = await OutboxRepository(session).requeue_failed(tenant_id=tenant_id)
    return RequeueResultRead(requeued=requeued)
