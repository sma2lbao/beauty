"""conversation 域 ORM 模型(conversations / chat_messages,对话功能)。

对话与任务(§6)的关系:一条用户消息按上下文要么触发一个新 run
(没有进行中的 run 时,创建后立即规划),要么并入当前 run 的追加指令
队列(§6.5);assistant 消息是 run 终态(交付物/失败原因)在对话里的
持久化投影——对话历史因此双向完整,可作多轮上下文。
"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column


class Conversation(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """一段持续对话:消息的容器,也是多轮上下文的边界。

    archived_at / deleted_at 采用软状态:
    - archived:用户主动归档,列表默认不展示,可取消;
    - deleted:软删除,列表/详情/消息一律按 404 处理,底层保留审计数据。
    """

    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(200), default="新对话")
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChatMessage(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """对话消息(user/assistant 双向,均持久化)。

    run_id 把消息与它关联的 run 绑起来:user 消息指向触发它的 run
    (新开的 run 或追加指令的目标 run);assistant 消息指向产出它的 run
    (run 到终态时落库)。历史回放按 created_at 升序。
    """

    __tablename__ = "chat_messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id"), index=True, nullable=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
