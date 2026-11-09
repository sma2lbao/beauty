"""run_trace_id:runs.trace_id(观测深链)

Revision ID: a9d3e5c7b2f4
Revises: d764787bf470
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3e5c7b2f4"
down_revision: str | None = "d764787bf470"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 可空、不回填:历史 run 没有 trace 信息,为空时控制台不显示深链。
    op.add_column("runs", sa.Column("trace_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "trace_id")
