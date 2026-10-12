"""账号域 ORM:组织(即租户)/ 成员 / 会话。

- `organizations.id` 就是租户标识,故**不混入 TenantMixin**(它是租户表本身);
- `users.subject` 是 IdP(Logto)的 sub,同租户内唯一;
- `sessions` 为多端 app 预留(§7)。
"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class Organization(UUIDMixin, TimestampMixin, Base):
    """组织 = 租户:名称、套餐与配额(§3)。"""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(120))
    plan: Mapped[str] = mapped_column(String(32), default="free")
    quota: Mapped[dict] = mapped_column(JSON, default=dict)


class User(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """组织成员:subject 来自 IdP,角色 owner/member。"""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "subject"),)

    subject: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default="member")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)


class Session(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """多端会话/设备(为 app 预留,§7):可登记、可吊销。"""

    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
