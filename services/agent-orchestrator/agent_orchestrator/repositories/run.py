"""run 仓储:run 列表/并发计数/变更扫描/超时中断扫描(按域拆分)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from database.repository import AccountRepository
from sqlalchemy import and_, func, or_, select

from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import RunStatus


class RunRepository(AccountRepository[Run]):
    model = Run
    not_found_code = "run.not_found"
    not_found_message = "run 不存在"
    id_detail_key = "run_id"

    async def list_recent(self, account_id: str, limit: int = 50) -> list[Run]:
        """账号内最新在前的 run 列表(id 兜底保证顺序确定)。"""
        return await self.list_scoped(
            account_id,
            limit=limit,
            order_by=[Run.created_at.desc(), Run.id.desc()],
        )

    async def list_for_conversation(
        self, account_id: str, conversation_id: str, limit: int = 50
    ) -> list[Run]:
        """会话内最新在前的 run 列表(post_message 据此找当前 run)。"""
        return await self.list_scoped(
            account_id,
            Run.conversation_id == conversation_id,
            limit=limit,
            order_by=[Run.created_at.desc(), Run.id.desc()],
        )

    async def count_active(self, account_id: str) -> int:
        """账号内未终结的 run 数(配额并发闸用)。"""
        terminal = (
            RunStatus.COMPLETED.value,
            RunStatus.FAILED.value,
            RunStatus.BLOCKED.value,
            RunStatus.CANCELLED.value,
        )
        return await self.count(
            Run.account_id == account_id, Run.status.not_in(terminal)
        )

    async def list_changed_since(
        self, account_id: str, since: datetime, limit: int = 100
    ) -> list[Run]:
        """最近有变更的 run(通知流用):updated_at >= since,按变更时间倒序。

        只扫活跃窗口内的行,避免全表扫描;账号隔离由 list_scoped 保证。
        """
        return await self.list_scoped(
            account_id,
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

        这是**跨账号的系统维护查询**(后台任务没有请求账号上下文),因此不用
        list_scoped;逐 run 处理时仍回到各自的账号作用域。阈值 <= 0 表示关闭该类。
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

    async def list_run_timeouts(
        self,
        now: datetime,
        *,
        default_timeout_seconds: int,
        limit: int = 100,
    ) -> list[Run]:
        """扫描执行超时的活跃 run(P1-C run 超时 sweeper 用)。

        跨账号的系统维护查询(同 list_stale_interrupts,后台任务没有请求账号
        上下文);逐 run 收尾时仍回到各自账号作用域。

        生效超时逐 run 不同(run.timeout_seconds 覆盖全局默认),先按
        coalesce(started_at, created_at) 最老在前取下候选,再在 Python 侧算是否
        真的超时——比在 SQL 里拼方言 epoch 函数可读,也避免时区/naive 差异。
        最老的排前面,所以 limit 截断不会漏掉真正超时的行。
        """
        active = (
            RunStatus.PENDING.value,
            RunStatus.PLANNING.value,
            RunStatus.RUNNING.value,
            RunStatus.AWAITING_APPROVAL.value,
            RunStatus.AWAITING_CONFIRMATION.value,
            RunStatus.AWAITING_INPUT.value,
            RunStatus.BLOCKED.value,
        )
        statement = (
            select(Run)
            .where(Run.status.in_(active))
            .order_by(func.coalesce(Run.started_at, Run.created_at), Run.id)
            .limit(limit)
        )
        runs = list(await self.session.scalars(statement))
        moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        due: list[Run] = []
        for run in runs:
            timeout = (
                run.timeout_seconds
                if run.timeout_seconds is not None
                else default_timeout_seconds
            )
            if timeout <= 0:
                continue
            started = run.started_at or run.created_at
            if started is None:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if (moment - started).total_seconds() >= timeout:
                due.append(run)
        return due
