"""后台作业仓储:登记、并发配额、条件收尾(仓储不提交)。

状态收尾一律走 BackgroundJobRepository.mark_finished 的条件 UPDATE
(WHERE status='running'):多副本并发 job_output / job_kill 时只有一方能把
running 推进到终态,输的一方拿到 False 后按"已被别人收尾"处理,不覆盖结果。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from database.repository import AccountRepository
from sqlalchemy import update

from tool_service.models.background_job import (
    JOB_STATUS_RUNNING,
    TERMINAL_JOB_STATUSES,
    BackgroundJob,
)


class BackgroundJobRepository(AccountRepository[BackgroundJob]):
    model = BackgroundJob
    not_found_code = "tool.not_found"
    not_found_message = "后台作业不存在"
    id_detail_key = "job_id"

    async def create(self, job: BackgroundJob) -> BackgroundJob:
        """登记一条 running 作业(flush 取主键,事务由调用方提交)。"""
        return await self.add(job)

    async def count_running(self, account_id: str) -> int:
        """该账号当前处于 running 的作业数(并发配额判定)。"""
        return await self.count(
            BackgroundJob.account_id == account_id,
            BackgroundJob.status == JOB_STATUS_RUNNING,
        )

    async def mark_finished(
        self,
        account_id: str,
        job_id: str,
        *,
        status: str,
        exit_code: int | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        """把 running 作业推进到终态;返回是否由本次调用完成收尾。

        rowcount 为 0 说明并发的另一方已经收尾(或作业行不存在/跨账号),
        调用方不得覆盖既有状态——这正是"多副本并发 job_kill 幂等"的落点。
        """
        moment = finished_at or datetime.now(UTC)
        result = await self.session.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == job_id,
                BackgroundJob.account_id == account_id,
                BackgroundJob.status == JOB_STATUS_RUNNING,
            )
            .values(
                status=status,
                exit_code=exit_code,
                finished_at=moment,
                updated_at=moment,
            )
        )
        return int(result.rowcount or 0) > 0

    async def list_terminal_before(
        self,
        account_id: str,
        before: datetime,
        *,
        limit: int | None = None,
    ) -> Sequence[BackgroundJob]:
        """该账号已收尾且更新早于 before 的作业(超保留期清理用)。"""
        return await self.list_scoped(
            account_id,
            BackgroundJob.status.in_(TERMINAL_JOB_STATUSES),
            BackgroundJob.updated_at < before,
            limit=limit,
            order_by=[BackgroundJob.updated_at],
        )

    async def delete_by_ids(self, account_id: str, job_ids: Sequence[str]) -> int:
        """按 id 批量删除(仅限本账号),返回删除行数。"""
        if not job_ids:
            return 0
        return await self.delete_where(account_id, BackgroundJob.id.in_(job_ids))
