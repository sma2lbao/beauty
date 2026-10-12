"""DeclarativeBase 与通用 mixin。

约定(docs/architecture.md §7):全平台所有 ORM 模型继承这里的 Base,
约束名走 NAMING_CONVENTION(alembic 迁移可回滚的前提);
多租户业务表必须混入 TenantMixin,查询永远带 tenant_id 过滤。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from database import NAMING_CONVENTION


class Base(DeclarativeBase):
    """全平台 ORM 基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDMixin:
    """32 位 hex UUID 主键(跨库一致、无连字符)。"""

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid4().hex
    )


class TimestampMixin:
    """created_at/updated_at:Python 侧生成(微秒精度),DB 侧兜底。

    只用 server_default 时,sqlite 的 CURRENT_TIMESTAMP 仅秒级精度——
    同秒写入的行排序不稳定("最新在前"的列表会闪)。Python 侧默认保证微秒级,
    server_default 留给绕过 ORM 的裸 SQL 插入。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class TenantMixin:
    """多租户隔离:业务表必带 tenant_id 并建索引。"""

    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
