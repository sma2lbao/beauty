"""工作区域 ORM:工作区与交付物元数据(§7:实体文件在对象存储)。

内容位置由 storage 决定:`database` 存 content(文本)/content_bytes(二进制)列;
`filesystem` 存 object_key 指向的对象,行里只留元数据。
"""

from __future__ import annotations

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class Workspace(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """工作区:团队的资料空间(同租户内名称唯一)。"""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)


class WorkspaceArtifact(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """交付物元数据(§7);`run_id` 仅作溯源,不建外键(跨服务不共享库表)。

    交付物可以是文本也可以是二进制:filename/content_type 是下载用的元数据,
    content 只放文本,content_bytes 只放二进制(两者互斥,且仅在 database 后端使用)。
    """

    __tablename__ = "workspace_artifacts"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="markdown")
    title: Mapped[str] = mapped_column(String(200))
    storage: Mapped[str] = mapped_column(String(16), default="database")
    object_key: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(128))
    content_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
