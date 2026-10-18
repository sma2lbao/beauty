"""runs:skill 快照列(M1 Skills)

Revision ID: c5e8a1f4b2d7
Revises: a3d7f2b9c1e5
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e8a1f4b2d7"
down_revision: str | None = "a3d7f2b9c1e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("skill_id", sa.String(length=80), nullable=True))
    op.add_column("runs", sa.Column("skill_system_prompt", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("skill_tools", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_runs_skill_id"), "runs", ["skill_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_skill_id"), table_name="runs")
    op.drop_column("runs", "skill_tools")
    op.drop_column("runs", "skill_system_prompt")
    op.drop_column("runs", "skill_id")
