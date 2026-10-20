"""沙箱 ORM:一行对应运行时里的一个沙箱实例(§7 阶段 B / P1)。

运行本体在 provider(microsandbox)侧,本表只保留租户、生命周期与配额元数据;
`name` 是 provider 全局唯一名,同时作为运行时句柄。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

# 生命周期:creating(已落库、运行时创建中)→ running → stopped;失败置 failed。
SANDBOX_STATUSES = ("creating", "running", "stopped", "failed")


class SandboxInstance(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """沙箱实例:租户隔离 + 生命周期 + 资源配额。"""

    __tablename__ = "sandboxes"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="generic")
    provider: Mapped[str] = mapped_column(String(32), default="microsandbox")
    external_name: Mapped[str] = mapped_column(String(128))
    image: Mapped[str] = mapped_column(String(120))
    cpus: Mapped[float] = mapped_column(Float, default=1.0)
    memory_mb: Mapped[int] = mapped_column(Integer, default=512)
    network: Mapped[str] = mapped_column(String(16), default="deny")
    workspace_id: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="creating", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    # 预留扩展(如实际生效的 ttl_seconds);列名用 meta,避开 Declarative 的 metadata
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON)
