"""run_subagents:runs 子代理委派列(P1-E)

Revision ID: d2c7f5b9a1e4
Revises: b9e4d1f7c2a8
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2c7f5b9a1e4"
down_revision: str | None = "b9e4d1f7c2a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 全部可空、无回填:顶层 run 与重试 run 的历史行没有子代理信息,
    # parent_kind 为空即"顶层/未知血缘"(重试血缘由 retry_run 在新建时补写)。
    op.add_column("runs", sa.Column("parent_kind", sa.String(length=16), nullable=True))
    op.add_column("runs", sa.Column("agent_name", sa.String(length=80), nullable=True))
    op.add_column("runs", sa.Column("agent_prompt", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("agent_tools", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "agent_tools")
    op.drop_column("runs", "agent_prompt")
    op.drop_column("runs", "agent_name")
    op.drop_column("runs", "parent_kind")
