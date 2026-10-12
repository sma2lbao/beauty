"""run 域 ORM 模型(runs / run_events / pending_instructions,架构 §6/§7)。"""

from __future__ import annotations

from typing import Any

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class Run(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """一次长任务执行。状态机与 goal 语义见架构 §6.2。"""

    __tablename__ = "runs"

    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    plan_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunEvent(UUIDMixin, TenantMixin, Base):
    """run 事件溯源:append-only、seq 每 run 单调递增(§6.3)。

    id 即 events.EventEnvelope 的事件 ID(消费幂等去重键);
    type 沿用信封命名,如 run.created.v1。
    """

    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_run_events_run_id_seq"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[str] = mapped_column(String(64))


class PendingInstruction(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """运行中追加的指令,当前 run 完成后依次触发(§6.5)。"""

    __tablename__ = "pending_instructions"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")


class RunArtifact(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """run 交付物:completed 时的成品(§7),阶段 A 每个 run 一件。

    内容先直接落库(读端可即时取件);阶段 B 转对象存储后这里只留引用。
    """

    __tablename__ = "run_artifacts"
    __table_args__ = (UniqueConstraint("run_id"),)

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="markdown")
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
