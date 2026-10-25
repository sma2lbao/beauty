"""长期记忆与对话摘要 ORM(记忆能力一期)。

对话摘要是压缩的产物:一条对话最多一行,记录摘要覆盖到哪条消息;记忆是跨对话
的长期事实,检索后拼进 system prompt。两者都按租户隔离;记忆可再按工作区收窄
(scope=workspace)或在全租户共享(scope=tenant)。
"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class ConversationSummary(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """对话摘要(压缩结果):一条对话一行,便于按 conversation_id 直接取用。"""

    __tablename__ = "conversation_summaries"
    __table_args__ = (UniqueConstraint("tenant_id", "conversation_id"),)

    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text)
    # 摘要覆盖到的最后一条消息时间:压缩时只统计 covered_until 之后的消息。
    covered_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    covered_messages: Mapped[int] = mapped_column(Integer, default=0)
    # 压缩前后的 token 估算,只用于评估压缩收益,不参与检索。
    tokens_before: Mapped[int] = mapped_column(Integer, default=0)
    tokens_after: Mapped[int] = mapped_column(Integer, default=0)


class Memory(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """长期记忆:可由手工/对话/run/工具写入的短文,带可插拔语义嵌入。"""

    __tablename__ = "memories"

    # scope=tenant 时为空(全租户可见);scope=workspace 时必填。
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="tenant")
    kind: Mapped[str] = mapped_column(String(16), default="note")
    content: Mapped[str] = mapped_column(Text)
    # 嵌入向量:默认本地哈希(local-hash-256,零网络),可切 llm-gateway 语义模型;
    # JSON 存原始向量,embedding_model 记录实际来源(混存时按维度降级为词面打分)。
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(64), default="local-hash-256")
    source_type: Mapped[str] = mapped_column(String(16), default="manual")
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    # 过期不物理删除,只置 deleted_at:泄露面更小,也便于事后审计。
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
