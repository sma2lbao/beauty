"""skill_packages:上传技能包与版本快照(P3)

新表 skill_packages(逻辑技能,租户内 slug 唯一)与 skill_versions(不可变版本:
对象存储引用 + manifest/SKILL.md 快照 + 执行字段);runs 补技能包冻结列。
内置技能仍随代码版本化,不写 DB。

Revision ID: e4c8b1f7a2d9
Revises: a8f3c1d6e2b4
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4c8b1f7a2d9"
down_revision: str | None = "a8f3c1d6e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_packages",
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_packages")),
        sa.UniqueConstraint(
            "tenant_id", "slug", name=op.f("uq_skill_packages_tenant_id")
        ),
    )
    op.create_index(
        op.f("ix_skill_packages_tenant_id"),
        "skill_packages",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "skill_versions",
        sa.Column("package_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("skill_md", sa.Text(), nullable=False),
        sa.Column("entrypoint", sa.JSON(), nullable=True),
        sa.Column("runtime", sa.String(length=16), nullable=True),
        sa.Column("network", sa.String(length=16), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["skill_packages.id"],
            name=op.f("fk_skill_versions_skill_packages_package_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill_versions")),
        sa.UniqueConstraint(
            "package_id", "version", name=op.f("uq_skill_versions_package_id")
        ),
    )
    op.create_index(
        op.f("ix_skill_versions_package_id"),
        "skill_versions",
        ["package_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_skill_versions_tenant_id"),
        "skill_versions",
        ["tenant_id"],
        unique=False,
    )
    # runs:上传技能包的冻结快照(内置技能只用既有 skill_id/prompt/tools)
    op.add_column("runs", sa.Column("skill_version_id", sa.String(length=32)))
    op.add_column("runs", sa.Column("skill_object_key", sa.String(length=512)))
    op.add_column("runs", sa.Column("skill_sha256", sa.String(length=64)))
    op.add_column("runs", sa.Column("skill_entrypoint", sa.JSON()))
    op.add_column("runs", sa.Column("skill_runtime", sa.String(length=16)))
    op.add_column("runs", sa.Column("skill_network", sa.String(length=16)))
    op.add_column("runs", sa.Column("skill_timeout_seconds", sa.Integer()))


def downgrade() -> None:
    for column in (
        "skill_timeout_seconds",
        "skill_network",
        "skill_runtime",
        "skill_entrypoint",
        "skill_sha256",
        "skill_object_key",
        "skill_version_id",
    ):
        op.drop_column("runs", column)
    op.drop_index(op.f("ix_skill_versions_tenant_id"), table_name="skill_versions")
    op.drop_index(op.f("ix_skill_versions_package_id"), table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index(op.f("ix_skill_packages_tenant_id"), table_name="skill_packages")
    op.drop_table("skill_packages")
