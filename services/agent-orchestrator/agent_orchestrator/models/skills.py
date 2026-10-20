"""技能包(P3):用户上传 SKILL.md 包的包/版本表。

内置技能仍随代码版本化(仓库 skills/*.json);上传包进 DB:

- skill_packages 是逻辑技能(租户内 slug 唯一),标题/描述随最近一次上传更新;
- skill_versions 是不可变版本:zip 本体在对象存储,行内只留
  object_key/sha256/size_bytes + manifest 快照 + SKILL.md 全文 + 执行字段;
- run 创建时把版本 ID 与执行字段冻结进 runs(见 models/runs.py),
  之后上传新版本不影响已建 run 的语义(技能包快照)。
"""

from __future__ import annotations

from typing import Any

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class SkillPackage(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """租户内一个逻辑技能(以 slug 标识,版本由 SkillVersion 承载)。"""

    __tablename__ = "skill_packages"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)

    slug: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")


class SkillVersion(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """一次上传产生的不可变版本(对象存储引用 + manifest/SKILL.md 快照)。

    manifest 是归一化后的 skill.json 快照(id/version/title/description/
    tools/entrypoint/runtime/network/timeout_seconds/has_scripts),仅供读路径
    重建 SkillManifest——SKILL.md 全文单独存在 skill_md,避免 JSON 重复大文本。
    """

    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("package_id", "version"),)

    package_id: Mapped[str] = mapped_column(ForeignKey("skill_packages.id"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    skill_md: Mapped[str] = mapped_column(Text)
    # 可执行技能的入口命令(如 ["python", "scripts/run.py"]);无入口为 None
    entrypoint: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    runtime: Mapped[str | None] = mapped_column(String(16), nullable=True)
    network: Mapped[str | None] = mapped_column(String(16), nullable=True)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
