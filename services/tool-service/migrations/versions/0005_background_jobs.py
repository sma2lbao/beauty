"""后台作业登记表(background_jobs)

Revision ID: 0005_background_jobs
Revises: 154f533a1698
Create Date: 2026-11-10 22:30:00.000000

job_start / job_output / job_kill 三工具的作业登记:账号隔离、沙箱定位
(sandbox_id + scope_key)、状态机(running → exited/killed/timeout/lost)。
只建表,不改既有迁移;downgrade 只删本表与索引。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_background_jobs"
down_revision: str | None = "154f533a1698"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("sandbox_id", sa.String(length=128), nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=True),
        sa.Column("command", sa.String(length=4000), nullable=False),
        sa.Column("cwd", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_background_jobs")),
    )
    op.create_index(
        "ix_background_jobs_account_created",
        "background_jobs",
        ["account_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_background_jobs_status",
        "background_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_background_jobs_account_id"),
        "background_jobs",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_background_jobs_account_id"), table_name="background_jobs")
    op.drop_index("ix_background_jobs_status", table_name="background_jobs")
    op.drop_index("ix_background_jobs_account_created", table_name="background_jobs")
    op.drop_table("background_jobs")
