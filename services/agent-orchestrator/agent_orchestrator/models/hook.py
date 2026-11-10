"""账号级应用 hooks:五个生命周期挂点 → 出站 webhook(docs/design/hooks.md §3)。

一行 = 一个事件 + 一个 matcher(工具名精确/glob)+ 一个 HTTPS target;同一事件
允许多条(按 created_at, id 升序串行,首个 deny/skip 短路),因此**不加唯一约束**。

``secret`` 是 HMAC 签名密钥,只在创建/轮换响应里返回一次;DB 明文存储是已记录
残余(§9,应复用 BYOK/MCP 的 envelope 加密)。``kind`` 本批只有 webhook,预留 shell。
"""

from __future__ import annotations

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import Boolean, Index, Integer, String, true
from sqlalchemy.orm import Mapped, mapped_column

#: 本批唯一支持的处理器类型(shell 留到有沙箱执行面之后)。
KIND_WEBHOOK = "webhook"


class AccountHook(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """一条账号 hook:事件 + matcher + webhook target。"""

    __tablename__ = "account_hooks"
    __table_args__ = (Index("ix_account_hooks_account_event", "account_id", "event"),)

    event: Mapped[str] = mapped_column(String(32))
    #: 工具名精确或 glob(仅 PreToolUse/PostToolUse 生效);"*" 匹配全部。
    matcher: Mapped[str] = mapped_column(String(200), default="*", server_default="*")
    kind: Mapped[str] = mapped_column(
        String(16), default=KIND_WEBHOOK, server_default=KIND_WEBHOOK
    )
    target: Mapped[str] = mapped_column(String(1024))
    secret: Mapped[str] = mapped_column(String(128))
    #: 实际超时取 min(本列, HOOKS_TIMEOUT_SECONDS),上限 30 秒。
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
