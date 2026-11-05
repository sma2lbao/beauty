"""连接 ORM:外部系统账号连接(账号隔离,凭证只进不出)。

凭证经 connector.core.crypto 信封加密后存 JSON 列(读端永不返回,
由 schema 层保证);密钥见配置 credential_secret(架构 §10 密钥库)。
provider 特有的附加字段统一收进 metadata 列(如工作区/账号标识等)。
"""

from __future__ import annotations

from datetime import datetime

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, Boolean, DateTime, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column


class Connection(UUIDMixin, TimestampMixin, AccountMixin, Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("account_id", "provider", "account"),
        {"comment": "同账号同提供商同外部账号只允许一条连接"},
    )

    provider: Mapped[str] = mapped_column(String(32), comment="provider 名")
    account: Mapped[str] = mapped_column(String(320), comment="稳定账号标识")
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(16), index=True, comment="active/revoked/expired"
    )
    credentials: Mapped[dict] = mapped_column(
        JSON, comment="提供商凭证;读端永不返回;Fernet 信封加密存储"
    )

    # ── 连接平台扩展(设计见 docs/design/connector.md §5.2)────────────────
    owner_subject: Mapped[str | None] = mapped_column(
        String(64), comment="授权用户 subject;无用户身份时为空"
    )
    auth_kind: Mapped[str | None] = mapped_column(
        String(16), comment="授权主体: user/token"
    )
    provider_account_id: Mapped[str | None] = mapped_column(
        String(128), comment="provider 账号 id"
    )
    external_workspace: Mapped[str | None] = mapped_column(
        String(128), comment="provider 工作区/账号标识"
    )
    scopes: Mapped[list | None] = mapped_column(JSON, comment="实际授予的 scope 列表")
    provider_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSON, comment="provider 附加信息(workspace/user/app 等)"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
        comment="关闭启用 != 解绑;默认 true",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="access token 过期时间"
    )
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="refresh token 过期时间(由 provider 决定)"
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次成功刷新时间"
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(64), comment="最近一次失败码(前端展示/判定重连)"
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次连通性测试时间"
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(64))
