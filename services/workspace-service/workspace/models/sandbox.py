"""沙箱 ORM:一行对应运行时里的一个沙箱实例(§7 阶段 B / P1)。

运行本体在 provider(microsandbox)侧,本表只保留租户、生命周期与配额元数据;
`name` 是 provider 全局唯一名,同时作为运行时句柄。

`scope_key` 是 tenant 内的幂等键(P0-05):tool-service 每次工具调用先
POST /v1/sandboxes:ensure,同一 (tenant_id, scope_key) 只应有 1 行;
普通 POST /v1/sandboxes 创建的沙箱该列为 NULL(不参与唯一)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

# 生命周期:creating(已落库、运行时创建中)→ running → stopped;失败置 failed。
SANDBOX_STATUSES = ("creating", "running", "stopped", "failed")

# 幂等键长度(设计 §11.2):conv:<conversation_id> / run:<run_id>,最长 96
SCOPE_KEY_MAX_LENGTH = 96
# 租户内唯一索引名(迁移里同名创建,alembic check 依赖它对齐)
TENANT_SCOPE_INDEX = "uq_sandboxes_tenant_scope"


class SandboxInstance(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """沙箱实例:租户隔离 + 生命周期 + 资源配额。"""

    __tablename__ = "sandboxes"
    __table_args__ = (
        # 部分唯一索引:NULL(非 ensure 创建的沙箱)不参与唯一,
        # 只有显式 scope_key 才约束——两种方言都支持的写法。
        Index(
            TENANT_SCOPE_INDEX,
            "tenant_id",
            "scope_key",
            unique=True,
            sqlite_where=text("scope_key IS NOT NULL"),
            postgresql_where=text("scope_key IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # ensure 的幂等键;为 NULL 时表示不是按 scope 复用的沙箱
    scope_key: Mapped[str | None] = mapped_column(
        String(SCOPE_KEY_MAX_LENGTH), index=True
    )
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
