"""create eval_report_downloads

Revision ID: 0003_eval_report_downloads
Revises: 0002_eval_report_artifact
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_eval_report_downloads"
down_revision = "0002_eval_report_artifact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_report_downloads",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=True),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("report_key", sa.String(length=256), nullable=True),
        sa.Column("report_size", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_eval_report_downloads")),
    )
    op.create_index(
        "ix_eval_report_downloads_created_at",
        "eval_report_downloads",
        ["created_at"],
    )
    op.create_index(
        "ix_eval_report_downloads_run",
        "eval_report_downloads",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_report_downloads_run", table_name="eval_report_downloads")
    op.drop_index(
        "ix_eval_report_downloads_created_at", table_name="eval_report_downloads"
    )
    op.drop_table("eval_report_downloads")
