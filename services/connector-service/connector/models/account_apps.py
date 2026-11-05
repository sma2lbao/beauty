"""账号级应用凭证覆盖(v1 不做管理 UI,先支持配置/脚本写入)。

解析顺序:账号行 -> 全局 Settings;app_secret 与连接凭证同一套信封加密。
"""

from __future__ import annotations

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class AccountApp(UUIDMixin, TimestampMixin, AccountMixin, Base):
    __tablename__ = "account_apps"
    __table_args__ = (
        UniqueConstraint("account_id", "provider"),
        {"comment": "按账号覆盖 provider 应用凭证"},
    )

    provider: Mapped[str] = mapped_column(String(32))
    app_id: Mapped[str] = mapped_column(String(128))
    app_secret_encrypted: Mapped[dict | None] = mapped_column(JSON)
    config: Mapped[dict | None] = mapped_column(JSON)
