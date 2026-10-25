"""read-before-write 的读记录表(P1)。

记录「哪个 run/conversation 在哪个沙箱 scope 读过哪个 path、读到的 sha256」。
write/edit 前由 tool-service 校验:没有记录 → tool.read_required;记录 sha 与
文件现状不同 → tool.stale_read。只由 tool-service 写入,不参与 run 事件。
"""

from __future__ import annotations

from datetime import UTC, datetime

from database.base import Base, TenantMixin, UUIDMixin
from sqlalchemy import DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column


class FileRead(UUIDMixin, TenantMixin, Base):
    """一次成功 read 的路径与内容摘要(按 tenant+scope+path upsert)。"""

    __tablename__ = "file_reads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "scope_key", "path", name="uq_file_reads_scope_path"
        ),
        Index("ix_file_reads_run", "tenant_id", "run_id", "path"),
    )

    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default=None)
    conversation_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    scope_key: Mapped[str] = mapped_column(String(96), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        nullable=False,
    )
