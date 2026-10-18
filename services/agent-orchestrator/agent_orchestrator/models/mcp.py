"""MCP Client 注册表(M1 本地版):租户配置第三方 MCP server。"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class MCPServer(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(500))
    # 只存密文/尾号;读端永不返回明文 token
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tools_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list)
    tools_cache: Mapped[list[dict]] = mapped_column(JSON, default=list)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
