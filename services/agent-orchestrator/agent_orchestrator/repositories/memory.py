"""长期记忆仓储:软删除 + 过期语义 + 后台清扫查询(按域拆分)。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from database.repository import TenantRepository
from sqlalchemy import and_, or_, select, update

from agent_orchestrator.models.memory import Memory


class MemoryRepository(TenantRepository[Memory]):
    """长期记忆仓储:软删除 + 过期视为不可见,purge 只标记不物理删。"""

    model = Memory
    not_found_code = "memory.not_found"
    not_found_message = "记忆不存在"
    id_detail_key = "memory_id"

    @staticmethod
    def _active_criteria(now: datetime) -> list[Any]:
        """ "仍然有效"的过滤条件:未软删,且未设置过期或尚未过期。"""
        return [
            Memory.deleted_at.is_(None),
            or_(Memory.expires_at.is_(None), Memory.expires_at > now),
        ]

    async def list_active(
        self,
        tenant_id: str,
        *,
        workspace_id: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        limit: int = 200,
    ) -> list[Memory]:
        """列出仍然有效的记忆。

        workspace_id 给定时返回"全租户可见(scope=tenant)"加"该工作区私有的
        (scope=workspace)"两类——上层不用区分作用域就能拿到当前工作区可用的记忆。
        workspace_id 未给定时不按工作区过滤(管理面要看到全部);召回/常驻注入这类
        没有工作区上下文的读路径必须显式传 scope="tenant",避免读到别的工作区的
        workspace 级记忆。
        """
        criteria = self._active_criteria(datetime.now(UTC))
        if workspace_id:
            criteria.append(
                or_(
                    Memory.scope == "tenant",
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
            tenant_id, *criteria, limit=limit or None, order_by=order_by
        )

    async def get_active(self, tenant_id: str, memory_id: str) -> Memory | None:
        """按 id 取仍然有效的记忆;已删除/已过期返回 None。"""
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                Memory.id == memory_id,
                *self._active_criteria(datetime.now(UTC)),
            )
        )

    async def soft_delete(self, tenant_id: str, memory_id: str) -> bool:
        """软删除:允许删除已过期但未清理的记忆;返回是否命中。"""
        memory = await self.session.scalar(
            self.scoped(
                tenant_id,
                Memory.id == memory_id,
                Memory.deleted_at.is_(None),
            )
        )
        if memory is None:
            return False
        memory.deleted_at = datetime.now(UTC)
        await self.session.flush()
        return True

    async def purge_expired(self, tenant_id: str) -> int:
        """把已过期且未删除的记忆标记为删除,返回本次清理条数。

        时间用 Python 侧 now 绑定(而不是 SQL now()),保证 SQLite/PG 行为一致。
        """
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(Memory)
            .where(
                Memory.tenant_id == tenant_id,
                Memory.deleted_at.is_(None),
                Memory.expires_at.isnot(None),
                Memory.expires_at <= now,
            )
            .values(deleted_at=now)
        )
        await self.session.flush()
        rowcount = getattr(result, "rowcount", 0) or 0
        return max(0, int(rowcount))

    async def counts(self, tenant_id: str) -> dict[str, int]:
        """计数:total 未删(含过期)/active 有效/pinned 有效且置顶/expired 已过期。"""
        now = datetime.now(UTC)
        tenant = Memory.tenant_id == tenant_id
        not_deleted = Memory.deleted_at.is_(None)
        alive = or_(Memory.expires_at.is_(None), Memory.expires_at > now)
        expired = and_(
            Memory.expires_at.isnot(None),
            Memory.expires_at <= now,
        )
        total = await self.count(tenant, not_deleted)
        active = await self.count(tenant, not_deleted, alive)
        pinned = await self.count(tenant, not_deleted, alive, Memory.pinned.is_(True))
        expired_count = await self.count(tenant, not_deleted, expired)
        return {
            "total": total,
            "active": active,
            "pinned": pinned,
            "expired": expired_count,
        }

    async def tenant_ids_with_active(self) -> list[str]:
        """有生效中记忆的租户 id(后台 sweeper 按租户逐轮遍历用)。

        后台任务没有请求上下文,拿不到单个 tenant_id,只能先扫一遍租户清单;
        按 id 排序让每轮处理顺序确定,便于日志对照。
        """
        rows = await self.session.scalars(
            select(Memory.tenant_id)
            .where(*self._active_criteria(datetime.now(UTC)))
            .distinct()
            .order_by(Memory.tenant_id)
        )
        return list(rows.all())
