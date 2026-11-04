"""conversation 域 ORM 模型(conversations / chat_messages,对话功能)。

对话与任务(§6)的关系:一条用户消息按上下文要么触发一个新 run
(没有进行中的 run 时,创建后立即规划),要么并入当前 run 的追加指令
队列(§6.5);assistant 消息是 run 终态(交付物/失败原因)在对话里的
持久化投影——对话历史因此双向完整,可作多轮上下文。
"""

from __future__ import annotations

from datetime import datetime

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


class Conversation(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """一段持续对话:消息的容器,也是多轮上下文的边界。

    archived_at / deleted_at 采用软状态:
    - archived:用户主动归档,列表默认不展示,可取消;
    - deleted:软删除,列表/详情/消息一律按 404 处理,底层保留审计数据。
    """

    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(200), default="新对话")
    # 工作区归属:对话必须落在某个工作区里(前端按工作区过滤会话)。
    # 弱引用(同 runs.conversation_id 惯例):workspace-service 的实体不在本库,
    # 加不了跨服务外键;归属正确性由服务层在创建时保证。
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 会话级模型偏好(P1-G,见 docs/design/model-routing.md §4):非空时本会话的
    # 直接对话与新建 run 默认用该模型;消息级显式指定优先于它。
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
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
    # 用户发消息时显式引用的 MCP server(§6.7):空列表 = 不限制(用全部已启用)
    mcp_server_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class ChatToolConfirmation(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """对话内写操作确认(§6.7 就地确认):WRITE 工具在 chat 里先生成待确认行。

    对话没有任务路径的审批闸,但产品要求"写操作就地确认":模型发起 risk=write
    的调用时,服务端**不执行**,只落一行 pending 并把 confirmation_id 交给前端卡片;
    用户点确认后由确认端点以 approved=True 执行(见 services/tool_confirmations.py)。

    - tool_call_id 是模型侧的调用 id:唯一约束保证同一次调用只生成一条确认,
      流式重放/重试不会生成重复卡片;
    - message_id 指向确认执行后落的 assistant 消息(pending 时为空);
    - result_text 保存工具原始输出(确认后回填),error_code 同步落工具错误码;
    - status 状态机:pending → confirmed / cancelled;过期由 expires_at 判定,
      读取方按时间返回 409(状态位不保证被改写,留待巡检清理)。
    """

    __tablename__ = "chat_tool_confirmations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_call_id"),
        Index(
            "ix_chat_tool_confirmations_tenant_conversation",
            "tenant_id",
            "conversation_id",
        ),
    )

    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_messages.id"), nullable=True
    )
    tool_call_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(120))
    risk: Mapped[str] = mapped_column(String(16))
    arguments: Mapped[str] = mapped_column(Text)  # 模型给的原始 JSON 字符串
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attachment(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """会话附件(本地最小版):内容存 DB,消息发送时再绑定 message_id。

    M0-12 不引入对象存储;生产多实例前改为共享存储/对象存储。
    """

    __tablename__ = "attachments"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("chat_messages.id"), index=True, nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    # 上传时解析出的文本(注入 Agent 上下文);解析失败只记错误,不拒绝附件
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
