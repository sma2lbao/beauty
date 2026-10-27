"""工具调用审计表。

每次工具执行(成功或失败)落一行:租户 + tool_call_id 唯一,支撑幂等重放与
运维查询;它不替代 orchestrator 的 run 事件流(run.tool.called.v1 仍是唯一
真源,见设计 8.4 / 12.3)。
"""

from __future__ import annotations

from datetime import UTC, datetime

from database.base import Base, TenantMixin, UUIDMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

DEFAULT_ARGUMENTS_CHARS = 2000


class ToolCall(UUIDMixin, TenantMixin, Base):
    """一次工具调用的审计记录(行级租户隔离)。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "tool_call_id", name="uq_tool_calls_tenant_call"),
        Index("ix_tool_calls_run", "tenant_id", "run_id", "created_at"),
        Index("ix_tool_calls_tool", "tenant_id", "tool_name", "created_at"),
    )

    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    # 主名(命中 alias 时也记主名,alias 只用在入站解析)。
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1")
    risk: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    arguments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    arguments_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # write/edit 成功后写入的字节数(单 run 配额按此累计;其他工具为 0)。
    bytes_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSON 数组字符串(相对路径),为空时存 "[]"。
    paths: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sha256_before: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    sha256_after: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    sandbox_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None
    )
    # 写工具的服务端 unified diff:只落审计库,不回给编排器/模型。
    patch: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    lines_added: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    lines_removed: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=None
    )
    patch_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
