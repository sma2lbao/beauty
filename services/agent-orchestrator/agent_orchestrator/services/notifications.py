"""站内通知:run 状态变化的持久化投影 + 未读清单 + SSE 实时推送。

旧实现(B1)只在客户端连接时做内存 diff,关掉页面通知就没了;现在改由后台
sweeper 把"需要人关注"的 run 状态幂等写成 notifications 行,SSE 只是这个表的
**增量尾巴**,列表端点提供未读清单。事实来源仍是 run_events —— 通知是投影,
丢了可以再扫出来。

口径(与前端 NOTIFY_ON 一致):待审批/待确认/等待回答/已完成/失败/被阻塞/已取消。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from common.logging import get_logger
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.core.sse import comment_frame, event_id_frame
from agent_orchestrator.repositories import NotificationRepository, RunRepository

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from agent_orchestrator.core.container import AppContainer

logger = get_logger(__name__)

#: 通知类别(落 notifications.kind)。
KIND_RUN_STATUS = "run.status"

#: 需要提醒的终态/人工介入态。
NOTIFY_STATUSES = frozenset(
    {
        "awaiting_approval",
        "awaiting_confirmation",
        "awaiting_input",
        "completed",
        "failed",
        "blocked",
        "cancelled",
    }
)

#: 状态 → (展示文案, 色调)。色调落库,客户端不再自己映射。
_STATUS_META: dict[str, tuple[str, str]] = {
    "awaiting_approval": ("任务待审批", "warning"),
    "awaiting_confirmation": ("任务待确认危险操作", "warning"),
    "awaiting_input": ("任务等待你的回答", "warning"),
    "completed": ("任务已完成", "success"),
    "failed": ("任务失败", "danger"),
    "blocked": ("任务被阻塞", "danger"),
    "cancelled": ("任务已取消", "info"),
}


def _as_utc(value: datetime) -> datetime:
    """sqlite 读回的是 naive(按 UTC 存);PG 是 aware。统一成 aware 再序列化。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _cursor(created_at: datetime, notification_id: str) -> str:
    """SSE 的 Last-Event-ID / 分页 before:created_at 微秒 + id 兜底唯一。"""
    return f"{_as_utc(created_at).isoformat()}|{notification_id}"


def _parse_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    created_at, separator, notification_id = value.rpartition("|")
    if not separator or not created_at or not notification_id:
        return None
    try:
        return datetime.fromisoformat(created_at), notification_id
    except ValueError:
        return None


def notification_for(run: Any) -> dict[str, Any] | None:
    """run → 通知字段;不需要提醒的状态返回 None。

    保留旧名字(api 层与测试都从这里取),字段扩展为落表所需。
    """
    status = str(run.status)
    meta = _STATUS_META.get(status)
    if meta is None:
        return None
    title, tone = meta
    run_title = getattr(run, "title", "") or ""
    goal = getattr(run, "goal", "") or ""
    conversation_id = getattr(run, "conversation_id", None)
    return {
        "kind": KIND_RUN_STATUS,
        "dedupe_key": f"{run.id}:{status}",
        "title": title,
        "body": (goal or run_title)[:300],
        "tone": tone,
        "to_path": (
            f"/?conversation={conversation_id}" if conversation_id else "/runs"
        ),
        "run_id": run.id,
        "conversation_id": conversation_id,
        "payload": {
            "run_id": run.id,
            "run_title": run_title,
            "goal": goal,
            "status": status,
        },
    }


async def sweep_once(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """扫一轮最近变化的 run,幂等落通知;顺手清理过期已读行。

    返回 {"created", "purged"}。写入冲突(另一副本先写)由唯一约束吞掉,
    不会整批失败——每条通知都是独立可判定的。
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(seconds=settings.notifications_lookback_seconds)
    created = 0
    purged = 0
    async with session_scope(factory) as session:
        runs = await RunRepository(session).list_notifiable_changed(
            since,
            statuses=tuple(NOTIFY_STATUSES),
            limit=settings.notifications_batch_limit,
        )
        repository = NotificationRepository(session)
        for run in runs:
            fields = notification_for(run)
            if fields is None:
                continue
            if await repository.insert_if_absent(
                account_id=run.account_id, **fields, created_at=moment
            ):
                created += 1
        cutoff = moment - timedelta(days=settings.notifications_retention_days)
        purged = await repository.purge_read_before(cutoff)
    if created or purged:
        logger.info("notification sweep completed", created=created, purged=purged)
    return {"created": created, "purged": purged}


async def sweeper_loop(container: AppContainer) -> None:
    """周期写入通知;间隔 <= 0 或没有数据库时直接退出(不算错误)。"""
    interval = float(container.settings.notifications_sweep_interval_seconds)
    if interval <= 0 or container.session_factory is None:
        return
    settings = container.settings
    while True:
        try:
            await sweep_once(container.session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("notification sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-notification-sweeper"
    )


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "tone": row.tone,
        "to_path": row.to_path,
        "run_id": row.run_id,
        "conversation_id": row.conversation_id,
        "payload": row.payload or {},
        "created_at": _iso(row.created_at),
        "read_at": _iso(row.read_at),
    }


async def list_notifications(
    factory: async_sessionmaker[AsyncSession],
    account_id: str,
    *,
    limit: int = 20,
    before: str | None = None,
) -> dict[str, Any]:
    """未读清单(最新在前);返回 items + unread_count + next_cursor。"""
    cursor = _parse_cursor(before)
    async with session_scope(factory) as session:
        repository = NotificationRepository(session)
        rows = await repository.list_recent(account_id, limit=limit + 1, before=cursor)
        unread = await repository.unread_count(account_id)
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "items": [_row_payload(row) for row in page],
        "unread_count": unread,
        "next_cursor": _cursor(page[-1].created_at, page[-1].id)
        if has_more and page
        else None,
    }


async def mark_read(
    factory: async_sessionmaker[AsyncSession],
    account_id: str,
    notification_id: str,
) -> dict[str, Any]:
    """单条已读(跨账号 404)。"""
    async with session_scope(factory) as session:
        row = await NotificationRepository(session).mark_read(
            account_id, notification_id
        )
        return _row_payload(row)


async def mark_all_read(
    factory: async_sessionmaker[AsyncSession], account_id: str
) -> dict[str, int]:
    """全部已读,返回本次更新的条数。"""
    async with session_scope(factory) as session:
        updated = await NotificationRepository(session).mark_all_read(account_id)
    return {"updated": updated}


async def notification_stream(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    account_id: str,
    last_event_id: str | None = None,
) -> AsyncIterator[str]:
    """账号级通知流:先给就绪帧,再按 (created_at, id) 游标追表。

    - 首帧 ": ready" 表示列表/回放已就绪(客户端此时应拉一次列表);
    - 带 Last-Event-ID 时从游标之后回放(断线不丢);不带则从"此刻"开始,
      避免一连接就吐历史——历史由列表端点提供;
    - 没有新行时按 sse_keepalive_polls 打 keep-alive。
    """
    cursor = _parse_cursor(last_event_id) or (datetime.now(UTC), "")
    yield comment_frame("ready")
    idle_polls = 0
    while True:
        async with session_scope(factory) as session:
            rows = await NotificationRepository(session).list_after(
                account_id,
                cursor,
                limit=settings.notifications_batch_limit,
            )
        if rows:
            idle_polls = 0
            for row in rows:
                event_id = _cursor(row.created_at, row.id)
                cursor = (row.created_at, row.id)
                yield event_id_frame(event_id, "notification.v1", _row_payload(row))
        else:
            idle_polls += 1
            if idle_polls % settings.sse_keepalive_polls == 0:
                yield comment_frame("keep-alive")
        await asyncio.sleep(settings.notifications_poll_interval_seconds)
