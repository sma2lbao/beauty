"""账号域 ORM:账号(users)/ 社交身份 / 会话 / 刷新令牌 / 用量 / BYOK / API Key。

- 账号即隔离边界:account_id = users.id(32 位 hex);users 表自身即账号主体,
  故**不混入 AccountMixin**,其余账号私有表一律混入;
- `users.subject` 是签发方的 sub(自建认证下即 email),全局唯一;
  `password_hash` 为空 = 尚未设置密码(社交登录首次建号);
- `quota` 是账号级配额(JSON):monthly_tokens / max_concurrent_runs,
  空对象 = 不限(fail-open);
- `sessions` 为多端 app 预留(§7);
- `refresh_tokens` 只存哈希(sha256),令牌本体只在签发响应里出现一次。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column


class User(UUIDMixin, TimestampMixin, Base):
    """账号:subject 来自 IdP(自建认证下 = email),全局唯一。"""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("subject"),)

    subject: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    # argon2 哈希(PHCF 格式,约 97 字符);空 = 未设密码
    password_hash: Mapped[str | None] = mapped_column(String(512))
    # 账号配额:monthly_tokens / max_concurrent_runs;空 = 不限(fail-open)
    quota: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class OAuthIdentity(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """社交登录身份:provider + subject 全局唯一,关联到账号。"""

    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    provider: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(191), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))


class Session(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """多端会话/设备(为 app 预留,§7):可登记、可吊销。"""

    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    device: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """用量事件(M0-11):run 级 token + 成本记录,只读聚合给用量页。

    ``cost_usd`` 为空 = 该模型未计价(BYOK 自定义模型/价目表缺项),
    与「0 元」是两回事:聚合时按 0 计入金额,但单独统计未计价条数,
    前端据此提示「有 N 次调用未计价」。
    """

    __tablename__ = "usage_events"
    # 可靠投递(P0-3):event_id 由「outbox 信封 id:样本序号」组成,
    # 同一账号内唯一——重复投递直接命中已有行,不会重复计费。
    __table_args__ = (UniqueConstraint("account_id", "event_id"),)

    #: 投递幂等键(旧调用/直连路径可为空,空值不参与唯一约束)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    model: Mapped[str] = mapped_column(String(128), default="unknown")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, index=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    billing_source: Mapped[str] = mapped_column(
        String(16), default="platform", server_default="platform"
    )


class BYOKKey(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """BYOK(M0-11):应用层加密存储 provider key + 该 provider 下的自定义模型清单。"""

    __tablename__ = "byok_keys"
    __table_args__ = (UniqueConstraint("account_id", "provider"),)

    provider: Mapped[str] = mapped_column(String(32), index=True)
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(16))
    # 自定义模型 id(如 "openai/gpt-4o-mini"):整体替换语义,空清单 = 无自定义模型
    models: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")


class ApiKey(UUIDMixin, AccountMixin, TimestampMixin, Base):
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
    """OAuth 2.1 动态注册的公开客户端(M3-04);全局表,授权时再绑定账号。"""

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_name: Mapped[str] = mapped_column(String(120))
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, default=list)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)


class OAuthAuthorizationCode(UUIDMixin, AccountMixin, TimestampMixin, Base):
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


class OAuthToken(UUIDMixin, AccountMixin, TimestampMixin, Base):
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


class RefreshToken(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """旋转式刷新令牌:本体即哈希。

    - 旋转:每次刷新把旧记录标 `revoked_at` 并签新记录(一 token 一次);
    - 已吊销令牌被再次使用视为**泄露信号**:吊销该账号全部刷新令牌(重用检测);
    - 账号隔离:与 sessions 同约定(account_id 由 AccountMixin 提供)。
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
