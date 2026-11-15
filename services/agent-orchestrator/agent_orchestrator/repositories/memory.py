"""长期记忆仓储:软删除 + 过期/失效语义 + 复核队列 + 后台清扫查询。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from database.repository import AccountRepository
from sqlalchemy import and_, or_, select, update

from agent_orchestrator.models.memory import Memory, MemoryReview


class MemoryRepository(AccountRepository[Memory]):
    """长期记忆仓储:软删除 + 过期视为不可见,purge 只标记不物理删。"""

    model = Memory
    not_found_code = "memory.not_found"
    not_found_message = "记忆不存在"
    id_detail_key = "memory_id"

    @staticmethod
    def _active_criteria(now: datetime) -> list[Any]:
        """ "仍然有效"的过滤条件:未软删、未过期、未被替代、已到生效时间。"""
        return [
            Memory.deleted_at.is_(None),
            or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            Memory.invalid_at.is_(None),
            or_(Memory.valid_from.is_(None), Memory.valid_from <= now),
        ]

    async def list_active(
        self,
        account_id: str,
        *,
        workspace_id: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        limit: int = 200,
    ) -> list[Memory]:
        """列出仍然有效的记忆。

        workspace_id 给定时返回"全账号可见(scope=account)"加"该工作区私有的
        (scope=workspace)"两类——上层不用区分作用域就能拿到当前工作区可用的记忆。
        workspace_id 未给定时不按工作区过滤(管理面要看到全部);召回/常驻注入这类
        没有工作区上下文的读路径必须显式传 scope="account",避免读到别的工作区的
        workspace 级记忆。
        """
        criteria = self._active_criteria(datetime.now(UTC))
        if workspace_id:
            criteria.append(
                or_(
                    Memory.scope == "account",
                    and_(
                        Memory.scope == "workspace",
                        Memory.workspace_id == workspace_id,
                    ),
                )
            )
        if kind:
            criteria.append(Memory.kind == kind)
        if scope:
            criteria.append(Memory.scope == scope)
        order_by = [
            Memory.pinned.desc(),
            Memory.updated_at.desc(),
            Memory.id.desc(),
        ]
        return await self.list_scoped(
            account_id, *criteria, limit=limit or None, order_by=order_by
        )

    async def get_active(self, account_id: str, memory_id: str) -> Memory | None:
        """按 id 取仍然有效的记忆;已删除/已过期返回 None。"""
        return await self.session.scalar(
            self.scoped(
                account_id,
                Memory.id == memory_id,
                *self._active_criteria(datetime.now(UTC)),
            )
        )

    async def get_visible(self, account_id: str, memory_id: str) -> Memory | None:
        """按 id 取未软删的记忆(含已过期/已失效):管理面要能查看替代链。"""
        return await self.session.scalar(
            self.scoped(
                account_id,
                Memory.id == memory_id,
                Memory.deleted_at.is_(None),
            )
        )

    async def list_visible(
        self,
        account_id: str,
        *,
        workspace_id: str | None = None,
        kind: str | None = None,
        source_type: str | None = None,
        include_invalid: bool = False,
        limit: int = 50,
    ) -> list[Memory]:
        """管理面列表:默认只列有效条目,include_invalid=True 时含失效条目。"""
        criteria: list[Any] = []
        now = datetime.now(UTC)
        if not include_invalid:
            criteria.extend(self._active_criteria(now))
        else:
            criteria.append(Memory.deleted_at.is_(None))
        if workspace_id:
            criteria.append(
                or_(
                    Memory.scope == "account",
                    and_(
                        Memory.scope == "workspace",
                        Memory.workspace_id == workspace_id,
                    ),
                )
            )
        if kind:
            criteria.append(Memory.kind == kind)
        if source_type:
            criteria.append(Memory.source_type == source_type)
        return await self.list_scoped(
            account_id,
            *criteria,
            limit=limit,
            order_by=[Memory.pinned.desc(), Memory.updated_at.desc(), Memory.id.desc()],
        )

    async def list_core(
        self,
        account_id: str,
        *,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[Memory]:
        """常驻注入的核心记忆:injection_mode=always,按 pinned/更新时间排序。"""
        criteria = self._active_criteria(datetime.now(UTC))
        criteria.append(Memory.injection_mode == "always")
        # procedure 有独立的约定块,这里排除避免同一内容注入两次。
        criteria.append(Memory.kind != "procedure")
        if workspace_id:
            criteria.append(
                or_(
                    Memory.scope == "account",
                    and_(
                        Memory.scope == "workspace",
                        Memory.workspace_id == workspace_id,
                    ),
                )
            )
        else:
            criteria.append(Memory.scope == "account")
        return await self.list_scoped(
            account_id,
            *criteria,
            limit=limit,
            order_by=[Memory.pinned.desc(), Memory.updated_at.desc(), Memory.id.desc()],
        )

    async def supersede(
        self,
        account_id: str,
        memory_ids: list[str],
        *,
        superseded_by: str,
        now: datetime,
    ) -> int:
        """把旧条目标记为已失效并指向替代者(保留正文,不物理删)。"""
        if not memory_ids:
            return 0
        result = await self.session.execute(
            update(Memory)
            .where(
                Memory.account_id == account_id,
                Memory.id.in_(memory_ids),
                Memory.deleted_at.is_(None),
                Memory.invalid_at.is_(None),
            )
            .values(invalid_at=now, superseded_by=superseded_by)
        )
        await self.session.flush()
        return max(0, int(getattr(result, "rowcount", 0) or 0))

    async def list_for_reembed(
        self, account_id: str, *, model: str, limit: int = 200
    ) -> list[Memory]:
        """列出向量缺失或由别的嵌入模型产出的记忆行(重建向量用)。

        只看未软删的行:已删除的数据不再参与检索,重建没有意义,也避免
        给合规删除留下"复活"路径。按创建时间正序,便于分批推进。
        """
        return await self.list_scoped(
            account_id,
            Memory.deleted_at.is_(None),
            or_(
                Memory.embedding.is_(None),
                Memory.embedding_model.is_(None),
                Memory.embedding_model != model,
            ),
            limit=limit,
            order_by=[Memory.created_at.asc(), Memory.id.asc()],
        )

    async def count_recent_writes(
        self, account_id: str, *, source_id: str, since: datetime
    ) -> int:
        """某来源(通常是 run)在时间窗内的写入条数:用于单 run 写入限流。"""
        return await self.count(
            Memory.account_id == account_id,
            Memory.source_id == source_id,
            Memory.deleted_at.is_(None),
            Memory.created_at >= since,
        )

    async def soft_delete(self, account_id: str, memory_id: str) -> bool:
        """软删除:允许删除已过期但未清理的记忆;返回是否命中。"""
        memory = await self.session.scalar(
            self.scoped(
                account_id,
                Memory.id == memory_id,
                Memory.deleted_at.is_(None),
            )
        )
        if memory is None:
            return False
        memory.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def purge_expired(self, account_id: str) -> int:
        """把已过期且未删除的记忆标记为删除,返回本次清理条数。

        时间用 Python 侧 now 绑定(而不是 SQL now()),保证 SQLite/PG 行为一致。
        """
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(Memory)
            .where(
                Memory.account_id == account_id,
                Memory.deleted_at.is_(None),
                Memory.expires_at.isnot(None),
                Memory.expires_at <= now,
            )
            .values(deleted_at=now)
        )
        await self.session.flush()
        rowcount = getattr(result, "rowcount", 0) or 0
        return max(0, int(rowcount))

    async def counts(self, account_id: str) -> dict[str, int]:
        """计数:total 未删(含过期)/active 有效/pinned 有效且置顶/expired 已过期。"""
        now = datetime.now(UTC)
        account = Memory.account_id == account_id
        not_deleted = Memory.deleted_at.is_(None)
        alive = or_(Memory.expires_at.is_(None), Memory.expires_at > now)
        expired = and_(
            Memory.expires_at.isnot(None),
            Memory.expires_at <= now,
        )
        invalid = Memory.invalid_at.isnot(None)
        valid_from_ok = or_(Memory.valid_from.is_(None), Memory.valid_from <= now)
        total = await self.count(account, not_deleted)
        active = await self.count(
            account, not_deleted, alive, valid_from_ok, Memory.invalid_at.is_(None)
        )
        pinned = await self.count(
            account,
            not_deleted,
            alive,
            valid_from_ok,
            Memory.invalid_at.is_(None),
            Memory.pinned.is_(True),
        )
        expired_count = await self.count(account, not_deleted, expired)
        superseded = await self.count(account, not_deleted, invalid)
        return {
            "total": total,
            "active": active,
            "pinned": pinned,
            "expired": expired_count,
            "superseded": superseded,
        }

    async def account_ids_with_active(self) -> list[str]:
        """有生效中记忆的账号 id(后台 sweeper 按账号逐轮遍历用)。

        后台任务没有请求上下文,拿不到单个 account_id,只能先扫一遍账号清单;
        按 id 排序让每轮处理顺序确定,便于日志对照。
        """
        rows = await self.session.scalars(
            select(Memory.account_id)
            .where(*self._active_criteria(datetime.now(UTC)))
            .distinct()
            .order_by(Memory.account_id)
        )
        return list(rows.all())


class MemoryReviewRepository(AccountRepository[MemoryReview]):
    """低置信度候选复核队列。"""

    model = MemoryReview
    not_found_code = "memory.review_not_found"
    not_found_message = "记忆复核项不存在"
    id_detail_key = "review_id"

    async def list_pending(
        self, account_id: str, *, limit: int = 50
    ) -> list[MemoryReview]:
        return await self.list_scoped(
            account_id,
            MemoryReview.status == "pending",
            limit=limit,
            order_by=[MemoryReview.created_at.desc(), MemoryReview.id.desc()],
        )

    async def count_pending(self, account_id: str) -> int:
        return await self.count(
            MemoryReview.account_id == account_id,
            MemoryReview.status == "pending",
        )
