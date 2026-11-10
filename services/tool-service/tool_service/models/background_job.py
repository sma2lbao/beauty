"""后台作业登记表(job_start / job_output / job_kill)。

作业本体是沙箱内的一个后台进程,本表只登记"谁在哪个沙箱里起了什么":
账号隔离 + 沙箱定位 + 状态机(running → exited/killed/timeout/lost)。
状态收尾一律走条件 UPDATE(见 BackgroundJobRepository.mark_finished),
多副本并发 job_kill 因此幂等;作业本身不保证 at-least-once(设计 §10)。
"""

from __future__ import annotations

from datetime import datetime

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

#: 作业状态:running 之外都是终态(终态不再变更,除非并发收尾失败)。
JOB_STATUS_RUNNING = "running"
JOB_STATUS_EXITED = "exited"
JOB_STATUS_KILLED = "killed"
JOB_STATUS_TIMEOUT = "timeout"
JOB_STATUS_LOST = "lost"
TERMINAL_JOB_STATUSES = (
    JOB_STATUS_EXITED,
    JOB_STATUS_KILLED,
    JOB_STATUS_TIMEOUT,
    JOB_STATUS_LOST,
)


class BackgroundJob(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """一次后台作业的登记记录(行级账号隔离)。"""

    __tablename__ = "background_jobs"
    __table_args__ = (
        # 列表/清理按 (账号, 时间) 扫;状态索引用在并发配额与收尾查询上。
        Index("ix_background_jobs_account_created", "account_id", "created_at"),
        Index("ix_background_jobs_status", "status"),
    )

    #: 作业进程所在的沙箱(与 tool_calls.sandbox_id 同口径,便于互查)。
    sandbox_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: 沙箱复用粒度(run: / conversation: / account:),与 exec 的 scope_key 同源。
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    conversation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    #: 发起 job_start 的那次工具调用(审计串联用;幂等重放不新建作业)。
    tool_call_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    name: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)
    command: Mapped[str] = mapped_column(String(4000), nullable=False)
    cwd: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JOB_STATUS_RUNNING
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
