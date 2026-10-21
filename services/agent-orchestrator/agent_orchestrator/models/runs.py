"""run 域 ORM 模型(runs / run_events / pending_instructions,架构 §6/§7)。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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
    # Skill 快照(M1):创建时冻结 manifest,后续技能版本变更不影响已建 run
    skill_id: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    skill_system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    skill_tools: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # 上传技能包快照(P3):冻结版本与执行字段,后续上传新版本不影响本 run。
    # 内置技能只填上面三列;上传包的 skill_id 是包 slug,版本/对象引用在下面。
    skill_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    skill_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    skill_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    skill_entrypoint: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    skill_runtime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skill_network: Mapped[str | None] = mapped_column(String(16), nullable=True)
    skill_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    # 对话消息显式引用的 MCP server 快照:非空时本 run 只装配这些 server 的工具
    mcp_server_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


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


class Job(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """run 执行队列(M0-04):SQLite 轮询 / PG 可换 SKIP LOCKED。

    plan/approve/confirm/resume/retry 都作为 job 入队;worker 领取后置
    running + lease,崩溃后 lease 过期可被重新领取。
    """

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_at", "status", "run_at"),)

    kind: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RunArtifact(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """run 交付物:一个 run 可以有多件(文本报告 + 技能/工具产出的文件)。

    内容位置由 storage 决定:`database` 存本表 `content`(文本)或
    `content_bytes`(二进制);`filesystem`(阶段 B)存对象存储,本表只留
    `object_key` 引用 + 元数据。

    `source` 标记产出来源(report / skill / mcp):主交付物优先取 report,
    二进制件不参与主文本交付。
    """

    __tablename__ = "run_artifacts"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="markdown")
    title: Mapped[str] = mapped_column(String(200))
    storage: Mapped[str] = mapped_column(String(16), default="database")
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 多件交付物的展示/下载元数据;旧行由迁移补 source='report'
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), default="report", server_default="report"
    )
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 二进制正文(database 模式):content 只放文本,二进制单独一列
    content_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # 与 workspace-service 产物的镜像幂等键(集成层按它判断"已同步过")
    workspace_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
