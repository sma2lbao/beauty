"""租户级应用凭证覆盖(v1 不做管理 UI,先支持配置/脚本写入)。

解析顺序:租户行 -> 全局 Settings;app_secret 与连接凭证同一套信封加密。
"""

from __future__ import annotations

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class TenantApp(UUIDMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "tenant_apps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider"),
        {"comment": "按租户覆盖 provider 应用凭证"},
    )

    provider: Mapped[str] = mapped_column(String(32))
    app_id: Mapped[str] = mapped_column(String(128))
    app_secret_encrypted: Mapped[dict | None] = mapped_column(JSON)
    config: Mapped[dict | None] = mapped_column(JSON)
