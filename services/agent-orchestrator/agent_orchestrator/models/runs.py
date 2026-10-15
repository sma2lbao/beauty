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
    # 状态原因:blocked(拒绝计划/验收未通过)与 failed(异常收敛)共用一列;
    # 具体含义由 status 决定,故不叫 blocked_reason
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 重试血缘:本 run 由哪个 failed run 派生(§6.2 failed 的重试语义是新起 run)
    parent_run_id: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    # 对话归属:由对话消息触发的 run 记录其会话;控制台直建的 run 为空。
    # 弱引用(同 parent_run_id 惯例):SQLite 的 ALTER TABLE 加不了外键约束,
    # 加 FK 会让迁移与模型不一致(autogenerate 反复生成 add_fk);归属正确性
    # 由业务层保证,强约束留给新建表的列(见 chat_messages)。
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )


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

    内容位置由 storage 决定:`database` 存本表 content 列;
    `filesystem`(阶段 B)存对象存储,本表只留 object_key 引用 + 元数据。
    """

    __tablename__ = "run_artifacts"
    __table_args__ = (UniqueConstraint("run_id"),)

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="markdown")
    title: Mapped[str] = mapped_column(String(200))
    storage: Mapped[str] = mapped_column(String(16), default="database")
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
