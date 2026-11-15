"""长期记忆与对话摘要 ORM(记忆能力一期)。

对话摘要是压缩的产物:一条对话最多一行,记录摘要覆盖到哪条消息;记忆是跨对话
的长期事实,检索后拼进 system prompt。两者都按账号隔离;记忆可再按工作区收窄
(scope=workspace)或在全账号共享(scope=account)。
"""

from __future__ import annotations

from datetime import datetime

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class ConversationSummary(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """对话摘要(压缩结果):一条对话一行,便于按 conversation_id 直接取用。"""

    __tablename__ = "conversation_summaries"
    __table_args__ = (UniqueConstraint("account_id", "conversation_id"),)

    conversation_id: Mapped[str] = mapped_column(String(32), index=True)
    summary: Mapped[str] = mapped_column(Text)
    # 摘要覆盖到的最后一条消息时间:压缩时只统计 covered_until 之后的消息。
    covered_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    covered_messages: Mapped[int] = mapped_column(Integer, default=0)
    # 压缩前后的 token 估算,只用于评估压缩收益,不参与检索。
    tokens_before: Mapped[int] = mapped_column(Integer, default=0)
    tokens_after: Mapped[int] = mapped_column(Integer, default=0)


class Memory(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """长期记忆:可由手工/对话/run/工具写入的短文,带可插拔语义嵌入。"""

    __tablename__ = "memories"

    # scope=account 时为空(全账号可见);scope=workspace 时必填。
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="account")
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
    # 事实时效(P3-B/R5):valid_from 之前不生效,invalid_at 之后视为已失效;
    # 被替代的旧条目保留正文并指向新条目,形成可追溯的替代链(不物理删)。
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invalid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_by: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    # 抽取置信度(0~1):低于阈值不自动落库,进复核队列;手工写入恒为 1。
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    # 注入模式:retrieved = 按查询相关性召回(默认);always = 每次请求常驻注入。
    # procedure 仍走约定的专用常驻块,不在此列。
    injection_mode: Mapped[str] = mapped_column(String(16), default="retrieved")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MemoryReview(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """低置信度抽取候选的复核队列(P3-B/R7)。

    自动抽取产生的候选先落这里而不是直接写 memories:人工确认后才成为长期记忆。
    队列只保存候选原文与证据,避免模型臆造的内容无声进入召回。
    """

    __tablename__ = "memory_reviews"

    workspace_id: Mapped[str | None] = mapped_column(
        String(32), index=True, nullable=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="account")
    kind: Mapped[str] = mapped_column(String(16), default="note")
    content: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # 抽取模型给出的对话原文依据(逐字引用),复核时用来判断真伪。
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(16), default="chat")
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    resolved_memory_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
