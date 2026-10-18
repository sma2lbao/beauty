"""通知流端点(P1-9b):按租户推送 run 状态变化。

设计:
- **服务端只做"变化探测"**,不引入新表:每轮扫描 updated_at 落在活跃窗口内的
  run,与前一轮快照 diff,只推**状态真正变化**且属于"需要提醒"的那几档;
- 客户端携带 Last-Event-ID 游标:游标是单调递增的序号,断线重连从游标之后继续;
- 首次连接只建基线,不推历史(与前端 watcher 行为一致,避免刷新弹一堆旧通知)。

为什么不做成持久化的通知表:当前诉求是"页面开着也能跨标签页/跨会话及时提醒",
事件本身已经在 run_events 里可回放,通知只是它的一个"提醒投影"。
真需要"关页面也能收"时(邮件/桌面推送),再落 notifications 表接 outbox。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from database.session import session_scope
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.api.deps import get_tenant_id
from agent_orchestrator.core.config import Settings
from agent_orchestrator.repositories import RunRepository

router = APIRouter(prefix="/notifications", tags=["notifications"])

#: 需要提醒的终态/人工介入态(与前端 watcher 的 NOTIFY_ON 保持同一口径)
NOTIFY_STATUSES = frozenset(
    {
        "awaiting_approval",
        "awaiting_confirmation",
        "completed",
        "failed",
        "blocked",
    }
)


def notification_for(run: Any) -> dict[str, Any] | None:
    """run → 通知载荷;不需要提醒的状态返回 None。"""
    if run.status not in NOTIFY_STATUSES:
        return None
    return {
        "run_id": run.id,
        "conversation_id": run.conversation_id,
        "title": run.title,
        "goal": run.goal,
        "status": run.status,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }


def diff_notifications(
    previous: dict[str, str], runs: list[Any]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """快照 diff:返回(新快照, 需要推送的通知列表)。

    - 首次见到某 run:只建基线(不推),避免连上就刷历史;
    - 状态变化且落在 NOTIFY_STATUSES:推;
    - 同一状态重复扫描不推(防抖)。
    """
    next_snapshot: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for run in runs:
        next_snapshot[run.id] = run.status
        previous_status = previous.get(run.id)
        if previous_status is None:
            continue  # 基线
        if previous_status == run.status:
            continue
        payload = notification_for(run)
        if payload is not None:
            out.append(payload)
    return next_snapshot, out


async def _scan(
    factory: async_sessionmaker[AsyncSession], tenant_id: str, settings: Settings
) -> list[Any]:
    since = datetime.now(UTC) - timedelta(
        seconds=settings.notifications_lookback_seconds
    )
    async with session_scope(factory) as session:
        return await RunRepository(session).list_changed_since(
            tenant_id, since, limit=settings.notifications_batch_limit
        )


async def notification_stream(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    tenant_id: str,
) -> AsyncIterator[str]:
    """租户级通知流:首帧建立基线,之后只推状态变化。"""
    snapshot: dict[str, str] = {}
    seq = 0
    idle_polls = 0
    first = True
    while True:
        runs = await _scan(factory, tenant_id, settings)
        if first:
            snapshot = {run.id: run.status for run in runs}
            first = False
            yield ": ready\n\n"
            await asyncio.sleep(settings.notifications_poll_interval_seconds)
            continue
        snapshot, notifications = diff_notifications(snapshot, runs)
        for payload in notifications:
            seq += 1
            yield (
                f"id: {seq}\nevent: run.status_changed.v1\n"
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            )
        if not notifications:
            idle_polls += 1
            if idle_polls % settings.sse_keepalive_polls == 0:
                yield ": keep-alive\n\n"
        await asyncio.sleep(settings.notifications_poll_interval_seconds)


@router.get("/stream")
async def stream_notifications(
    request: Request,
    tenant_id: Annotated[str, Depends(get_tenant_id)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """订阅租户内 run 状态变化(SSE)。

    Last-Event-ID 当前只用于兼容重连语义(服务端不做回放:通知是提醒投影,
    漏掉一条事件等价于多一次轮询,客户端会从 /runs 列表补齐真实状态)。
    """
    del last_event_id
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    settings: Settings = request.app.state.settings
    return StreamingResponse(
        notification_stream(factory, settings, tenant_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
