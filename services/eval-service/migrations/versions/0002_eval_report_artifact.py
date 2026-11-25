"""eval 报告对象存储:report_key/report_size

Revision ID: 0002_eval_report_artifact
Revises: 0001_eval_runs
Create Date: 2026-11-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_eval_report_artifact"
down_revision = "0001_eval_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eval_runs",
        sa.Column("report_key", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "eval_runs",
        sa.Column(
            "report_size", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("eval_runs", "report_size")
    op.drop_column("eval_runs", "report_key")
