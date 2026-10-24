"""账号域 ORM:组织(即租户)/ 成员 / 会话 / 刷新令牌。

- `organizations.id` 就是租户标识,故**不混入 TenantMixin**(它是租户表本身);
- `users.subject` 是签发方的 sub(自建认证下即 email),同租户内唯一;
  `password_hash` 为空 = 尚未设置密码(存量/被邀请用户);
- `sessions` 为多端 app 预留(§7);
- `refresh_tokens` 只存哈希(sha256),令牌本体只在签发响应里出现一次。
"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    # argon2 哈希(PHCF 格式,约 97 字符);空 = 未设密码
    password_hash: Mapped[str | None] = mapped_column(String(512))


class OAuthIdentity(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """社交登录身份:provider + subject 全局唯一,关联到租户成员。"""

    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    provider: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(191), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))


class Session(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """多端会话/设备(为 app 预留,§7):可登记、可吊销。"""

    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """用量事件(M0-11):run 级 token 记录,只读聚合给用量页。"""

    __tablename__ = "usage_events"

    run_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="unknown")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, index=True)


class BYOKKey(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """BYOK 占位(M0-11):应用层加密存储,暂不参与模型调用。"""

    __tablename__ = "byok_keys"
    __table_args__ = (UniqueConstraint("tenant_id", "provider"),)

    provider: Mapped[str] = mapped_column(String(32), index=True)
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))


class ApiKey(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """开放平台 API Key(M3-01):只存 SHA-256 哈希,明文仅创建/轮换时返回一次。"""

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_hint: Mapped[str] = mapped_column(String(8))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OAuthClient(UUIDMixin, TimestampMixin, Base):
    """OAuth 2.1 动态注册的公开客户端(M3-04);不带租户,授权时再绑定租户。"""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_name: Mapped[str] = mapped_column(String(120))
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, default=list)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)


class OAuthAuthorizationCode(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    redirect_uri: Mapped[str] = mapped_column(String(500))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    code_challenge: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OAuthToken(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "oauth_tokens"

    access_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RefreshToken(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """旋转式刷新令牌:本体即哈希。

    - 旋转:每次刷新把旧记录标 `revoked_at` 并签新记录(一 token 一次);
    - 已吊销令牌被再次使用视为**泄露信号**:吊销该用户全部刷新令牌(重用检测);
    - 租户隔离:与 users/sessions 同约定。
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
