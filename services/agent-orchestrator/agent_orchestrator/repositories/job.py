"""job 仓储:领取租约(方言分叉)与去重/取消(按域拆分)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from database.repository import AccountRepository
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories.constants import QUEUED


def _claim_statement(now: datetime):
    """job 领取条件:queued 已到期,或 running 但租约过期(worker 崩溃后重领)。"""
    return (
        select(Job)
        .where(
            or_(
                and_(Job.status == "queued", Job.run_at <= now),
                and_(
                    Job.status == "running",
                    Job.lease_until.is_not(None),
                    Job.lease_until < now,
                ),
            )
        )
        # P1-C 调度优先级:越大越先;同优先级按到期时间/入队时间 FIFO。
        .order_by(Job.priority.desc(), Job.run_at, Job.created_at)
        .limit(1)
    )


def _is_postgres(session: Session) -> bool:
    """领取策略按方言分叉:PG 用行锁跳过,SQLite 没有 FOR UPDATE。"""
    bind = session.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


class JobRepository(AccountRepository[Job]):
    model = Job
    not_found_code = "job.not_found"
    not_found_message = "任务不存在"
    id_detail_key = "job_id"

    async def find_queued(self, account_id: str, kind: str, run_id: str) -> Job | None:
        """同一 run 的同类 job 去重(避免用户连点产生重复执行)。"""
        return await self.session.scalar(
            self.scoped(
                account_id,
                Job.kind == kind,
                Job.run_id == run_id,
                Job.status.in_(("queued", "running")),
            ).order_by(Job.created_at)
        )

    async def cancel_queued_for_run(self, account_id: str, run_id: str) -> int:
        """把该 run 还没被领取的 job 置为 cancelled(取消 run 时用)。

        只动 queued:running 的 job 由执行边界自己收尾(协作式取消),
        把 running 改成 cancelled 会和 worker 的 succeeded 回写打架。
        返回取消条数,便于事件/指标记录。
        """
        rows = await self.session.scalars(
            self.scoped(
                account_id,
                Job.run_id == run_id,
                Job.status == QUEUED,
            )
        )
        cancelled = 0
        for job in rows:
            job.status = "cancelled"
            job.lease_until = None
            cancelled += 1
        return cancelled

    async def claim_next(
        self, worker_id: str, now: datetime, lease_seconds: int
    ) -> Job | None:
        """领取一个到期的 job,并立刻占住租约。

        SQLite(单进程 dev)靠先查后改即可;PostgreSQL 多实例部署时同一时刻会有
        多个 worker 抢同一行,因此套 `FOR UPDATE SKIP LOCKED`:被占住的行直接跳过,
        各 worker 领到不同 job,不互相等锁也不重复执行。
        """
        statement = _claim_statement(now)
        if _is_postgres(self.session):
            statement = statement.with_for_update(skip_locked=True)
        job = await self.session.scalar(statement)
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_until = now + timedelta(seconds=lease_seconds)
        return job

    async def renew_lease(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        """续租:只有仍由本 worker 持锁的 running job 才更新,返回是否续上。

        worker 执行期间由 heartbeat 周期调用;返回 False 表示租约已丢
        (被别的 worker 重领,或 job 已被收尾),调用方应停止续租。
        """
        moment = now or datetime.now(UTC)
        result = await self.session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "running",
                Job.locked_by == worker_id,
            )
            .values(lease_until=moment + timedelta(seconds=lease_seconds))
        )
        return bool(result.rowcount)

    async def mark_succeeded(self, job_id: str, worker_id: str) -> bool:
        """收尾成功:仅当仍由本 worker 持锁时生效,避免覆盖别人的执行结果。"""
        result = await self.session.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "running",
                Job.locked_by == worker_id,
            )
            .values(status="succeeded", lease_until=None, locked_by=None)
        )
        return bool(result.rowcount)

    async def finish_failure(
        self,
        job_id: str,
        worker_id: str,
        *,
        error: str,
        now: datetime | None = None,
    ) -> bool:
        """收尾失败:按尝试次数决定重排或落 failed;仅当仍持锁时生效。"""
        moment = now or datetime.now(UTC)
        statement = select(Job).where(
            Job.id == job_id,
            Job.status == "running",
            Job.locked_by == worker_id,
        )
        if _is_postgres(self.session):
            statement = statement.with_for_update()
        job = await self.session.scalar(statement)
        if job is None:
            return False
        job.last_error = error[:1000]
        job.lease_until = None
        job.locked_by = None
        if job.attempts >= job.max_attempts:
            job.status = "failed"
        else:
            job.status = "queued"
            job.run_at = moment + timedelta(seconds=min(2**job.attempts, 30))
        await self.session.flush()
        return True
