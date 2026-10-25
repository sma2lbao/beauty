"""通知流的服务端扫描:只读"变更探测",不引入通知表。

通知只是 run 事件的一个提醒投影:每轮扫 updated_at 落在活跃窗口内的 run,
由 api 层做快照 diff。持久化通知表等"关页面也能收"的诉求出现时再加。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.repositories import RunRepository


async def scan_changed(
    factory: async_sessionmaker[AsyncSession], tenant_id: str, settings: Settings
) -> list[Any]:
    """扫描租户内活跃窗口里最近变化的 run(只读,独立短事务)。"""
    since = datetime.now(UTC) - timedelta(
        seconds=settings.notifications_lookback_seconds
    )
    async with session_scope(factory) as session:
        return await RunRepository(session).list_changed_since(
            tenant_id, since, limit=settings.notifications_batch_limit
        )
