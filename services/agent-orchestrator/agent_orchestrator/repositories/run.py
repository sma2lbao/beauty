"""run 仓储:run 列表/并发计数/变更扫描/超时中断扫描(按域拆分)。"""

from __future__ import annotations

from datetime import datetime, timedelta

from database.repository import TenantRepository
from sqlalchemy import and_, or_, select

from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import RunStatus


class RunRepository(TenantRepository[Run]):
    model = Run
    not_found_code = "run.not_found"
    not_found_message = "run 不存在"
    id_detail_key = "run_id"

    async def list_recent(self, tenant_id: str, limit: int = 50) -> list[Run]:
        """租户内最新在前的 run 列表(id 兜底保证顺序确定)。"""
        return await self.list_scoped(
            tenant_id,
            limit=limit,
            order_by=[Run.created_at.desc(), Run.id.desc()],
        )

    async def list_for_conversation(
        self, tenant_id: str, conversation_id: str, limit: int = 50
    ) -> list[Run]:
        """会话内最新在前的 run 列表(post_message 据此找当前 run)。"""
        return await self.list_scoped(
            tenant_id,
            Run.conversation_id == conversation_id,
            limit=limit,
            order_by=[Run.created_at.desc(), Run.id.desc()],
        )

    async def count_active(self, tenant_id: str) -> int:
        """租户内未终结的 run 数(配额并发闸用)。"""
        terminal = (
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.BLOCKED.value,
            RunStatus.CANCELLED.value,
        )
        return await self.count(Run.tenant_id == tenant_id, Run.status.not_in(terminal))

    async def list_changed_since(
        self, tenant_id: str, since: datetime, limit: int = 100
    ) -> list[Run]:
        """最近有变更的 run(通知流用):updated_at >= since,按变更时间倒序。

        只扫活跃窗口内的行,避免全表扫描;租户隔离由 list_scoped 保证。
        """
        return await self.list_scoped(
            tenant_id,
            Run.updated_at >= since,
            limit=limit,
            order_by=[Run.updated_at.desc(), Run.id.desc()],
        )

    async def list_stale_interrupts(
        self,
        now: datetime,
        *,
        approval_seconds: int,
        confirmation_seconds: int,
        question_seconds: int = 0,
        limit: int = 100,
    ) -> list[Run]:
        """扫描停留超时的待审批/待确认/待回答 run(中断超时 sweeper 用)。

        这是**跨租户的系统维护查询**(后台任务没有请求租户上下文),因此不用
        list_scoped;逐 run 处理时仍回到各自的租户作用域。阈值 <= 0 表示关闭该类。
        """
        criteria = []
        if approval_seconds > 0:
            criteria.append(
                and_(
                    Run.status == RunStatus.AWAITING_APPROVAL.value,
                    Run.updated_at < now - timedelta(seconds=approval_seconds),
                )
            )
        if confirmation_seconds > 0:
            criteria.append(
                and_(
                    Run.status == RunStatus.AWAITING_CONFIRMATION.value,
                    Run.updated_at < now - timedelta(seconds=confirmation_seconds),
                )
            )
        if question_seconds > 0:
            criteria.append(
                and_(
                    Run.status == RunStatus.AWAITING_INPUT.value,
                    Run.updated_at < now - timedelta(seconds=question_seconds),
                )
            )
        if not criteria:
            return []
        statement = (
            select(Run)
            .where(or_(*criteria))
            .order_by(Run.updated_at, Run.id)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))
