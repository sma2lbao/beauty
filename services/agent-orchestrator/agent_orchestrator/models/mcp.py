"""MCP Client 注册表(M1 本地版):租户配置第三方 MCP server。

P2 增加 **stdio** 传输:命令在沙箱(workspace-service /v1/sandboxes)里拉起,
经 stdin/stdout JSON-RPC 调用。两种传输共用一张表:

- `transport` 选 `http`(默认,进程内 httpx)或 `stdio`(沙箱内进程);
- stdio 用 `command`/`args`/`encrypted_env`/`runtime`/`image`,**url 存空字符串**
  (列保持非空是历史契约,不为一次新增字段改列可空性);
- `encrypted_env` 与 token 同口径:只存密文,读端永不返回值;
- `auto_approve` 是**可信标记**:默认 False(每次调用都进确认闸);置 True 后
  该 server 的工具在对话里可直接执行、任务里也不再逐次确认——对齐
  docs/architecture.md §6.4"操作策略是配置,不是硬编码"。
"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class MCPServer(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str] = mapped_column(String(80))
    # http 时是 MCP 端点;stdio 时存空字符串(见模块 docstring)
    url: Mapped[str] = mapped_column(String(500))
    # 传输方式:http | stdio
    transport: Mapped[str] = mapped_column(String(16), default="http")
    # stdio:要拉起的命令与参数(命令必须命中 allowlist,服务层校验)
    command: Mapped[str | None] = mapped_column(String(200), nullable=True)
    args: Mapped[list[str]] = mapped_column(JSON, default=list)
    # stdio 子进程的运行时:python | node(决定写哪个 probe 脚本)
    runtime: Mapped[str] = mapped_column(String(16), default="python")
    image: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 只存密文/尾号;读端永不返回明文 token
    encrypted_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # stdio:env 的 JSON dict 密文(读端只回 key 名)
    encrypted_env: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 免确认执行(可信 server):对话路径只有置 True 的工具才会被真正调用
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    tools_allowlist: Mapped[list[str]] = mapped_column(JSON, default=list)
    tools_cache: Mapped[list[dict]] = mapped_column(JSON, default=list)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
