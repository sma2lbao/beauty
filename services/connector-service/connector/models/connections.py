"""连接 ORM:外部系统账号连接(租户隔离,凭证只进不出)。

凭证经 connector.core.crypto 信封加密后存 JSON 列(读端永不返回,
由 schema 层保证);密钥见配置 credential_secret(架构 §10 密钥库)。
"""

from __future__ import annotations

from datetime import datetime

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class Connection(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "account"),
        {"comment": "同租户同提供商同账号只允许一条连接"},
    )

    provider: Mapped[str] = mapped_column(
        String(32), comment="历史数据;无内置 provider"
    )
    account: Mapped[str] = mapped_column(String(320), comment="账号标识(邮箱地址)")
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(16), index=True, comment="active/revoked/expired"
    )
    credentials: Mapped[dict] = mapped_column(
        JSON, comment="提供商凭证;读端永不返回;密文存储为阶段 B"
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次连通性测试时间"
    )
