"""tool_calls 增加服务端补丁审计列(补丁落库)

Revision ID: 0003_tool_call_patches
Revises: 0002_file_reads
Create Date: 2026-10-27 22:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_tool_call_patches"
down_revision: str | None = "0002_file_reads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("patch", sa.Text(), nullable=True))
    op.add_column("tool_calls", sa.Column("lines_added", sa.Integer(), nullable=True))
    op.add_column(
        "tool_calls", sa.Column("lines_removed", sa.Integer(), nullable=True)
    )
    op.add_column(
        "tool_calls",
        sa.Column(
            "patch_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_column("patch_truncated")
        batch_op.drop_column("lines_removed")
        batch_op.drop_column("lines_added")
        batch_op.drop_column("patch")
