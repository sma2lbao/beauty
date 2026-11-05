"""授权会话 ORM:state 只落随机 nonce,身份靠服务端行查回(RFC 9700)。

一次性:回调成功即置 completed,重复 nonce 一律拒绝;过期 TTL 由服务配置。
"""

from __future__ import annotations

from datetime import datetime

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column


class OAuthSession(UUIDMixin, TimestampMixin, AccountMixin, Base):
    __tablename__ = "oauth_sessions"
    __table_args__ = {"comment": "一次性 OAuth 授权会话(nonce 换身份)"}

    owner_subject: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(32))
    state_nonce: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code_verifier: Mapped[str | None] = mapped_column(String(128))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    scopes: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(16), index=True, comment="pending/completed/failed/expired"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    connection_id: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
