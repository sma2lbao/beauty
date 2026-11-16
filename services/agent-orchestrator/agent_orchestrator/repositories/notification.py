"""站内通知仓储:幂等写入 + 未读游标 + 保留期清理。

写入必须幂等:后台 sweeper 每轮都会重扫同一批 run,多副本之间也可能同时写。
带 account_id 的读写一律经 AccountRepository 的 scoped 查询;保留期清理是
**跨账号的系统维护操作**,与 alerts 的 purge 同口径(后台任务没有请求账号)。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from database.repository import AccountRepository
from sqlalchemy import and_, delete, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agent_orchestrator.models.notification import Notification

#: 列表游标:(created_at, id)。同一条通知的 created_at 可能同微秒,
#: 用 id 做 tie-break 才能保证分页/回放不漏不重。
Cursor = tuple[datetime, str]


def _dialect_name(session: Any) -> str:
    bind = session.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", "")


class NotificationRepository(AccountRepository[Notification]):
    model = Notification
    not_found_code = "notification.not_found"
    not_found_message = "通知不存在"
    id_detail_key = "notification_id"

    async def insert_if_absent(
        self,
        *,
        account_id: str,
        kind: str,
        dedupe_key: str,
        title: str,
        body: str,
        tone: str,
        to_path: str | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> bool:
        """写一条通知;同 (account_id, kind, dedupe_key) 已存在时静默跳过。

        返回是否真的插入。PG/SQLite 走 ON CONFLICT DO NOTHING(多副本并发安全);
        其他方言退化为先查后写(仅用于未支持的数据库,不保证并发安全)。
        """
        moment = created_at or datetime.now(UTC)
        values: dict[str, Any] = {
            "id": uuid4().hex,
            "account_id": account_id,
            "kind": kind,
            "dedupe_key": dedupe_key,
            "title": title,
            "body": body,
            "tone": tone,
            "to_path": to_path,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "payload": payload or {},
            "read_at": None,
            "created_at": moment,
            "updated_at": moment,
        }
        dialect = _dialect_name(self.session)
        if dialect in ("postgresql", "sqlite"):
            factory = pg_insert if dialect == "postgresql" else sqlite_insert
            statement = (
                factory(Notification)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["account_id", "kind", "dedupe_key"]
                )
            )
            result = await self.session.execute(statement)
            return bool(result.rowcount)
        if await self.exists_for_account(
            account_id,
            Notification.kind == kind,
            Notification.dedupe_key == dedupe_key,
        ):
            return False
        self.session.add(Notification(**values))
        await self.session.flush()
        return True

    async def list_recent(
        self,
        account_id: str,
        *,
        limit: int = 20,
        before: Cursor | None = None,
    ) -> list[Notification]:
        """未读与已读混排,最新在前;before 为上一页末条的 (created_at, id)。"""
        criteria = []
        if before is not None:
            created_at, notification_id = before
            criteria.append(
                or_(
                    Notification.created_at < created_at,
                    and_(
                        Notification.created_at == created_at,
                        Notification.id < notification_id,
                    ),
                )
            )
        return await self.list_scoped(
            account_id,
            *criteria,
            limit=limit,
            order_by=[Notification.created_at.desc(), Notification.id.desc()],
        )

    async def list_after(
        self,
        account_id: str,
        after: Cursor,
        *,
        limit: int = 50,
    ) -> list[Notification]:
        """SSE 回放:严格晚于游标的通知,按时间正序。"""
        created_at, notification_id = after
        return await self.list_scoped(
            account_id,
            or_(
                Notification.created_at > created_at,
                and_(
                    Notification.created_at == created_at,
                    Notification.id > notification_id,
                ),
            ),
            limit=limit,
            order_by=[Notification.created_at, Notification.id],
        )

    async def unread_count(self, account_id: str) -> int:
        return await self.count(
            Notification.account_id == account_id,
            Notification.read_at.is_(None),
        )

    async def mark_read(
        self, account_id: str, notification_id: str, *, now: datetime | None = None
    ) -> Notification:
        """标记单条已读(跨账号 404);已读的再点不会改时间。"""
        notification = await self.require_for_account(account_id, notification_id)
        if notification.read_at is None:
            notification.read_at = now or datetime.now(UTC)
            await self.session.flush()
        return notification

    async def mark_all_read(
        self, account_id: str, *, now: datetime | None = None
    ) -> int:
        """把账号内所有未读置为已读,返回条数。"""
        result = await self.session.execute(
            update(Notification)
            .where(
                Notification.account_id == account_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=now or datetime.now(UTC))
        )
        return int(result.rowcount or 0)

    async def purge_read_before(self, cutoff: datetime) -> int:
        """跨账号清理:删掉 cutoff 之前已读的行(未读是待办,永不自动删)。"""
        result = await self.session.execute(
            delete(Notification).where(
                Notification.read_at.is_not(None),
                Notification.created_at < cutoff,
            )
        )
        return int(result.rowcount or 0)
