"""file_reads 表与 tool_calls.bytes_written(P1 受控写)

Revision ID: 0002_file_reads
Revises: 0001_tool_calls
Create Date: 2026-10-25 16:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_file_reads"
down_revision: str | None = "0001_tool_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_calls",
        sa.Column(
            "bytes_written", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_table(
        "file_reads",
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("conversation_id", sa.String(length=32), nullable=True),
        sa.Column("scope_key", sa.String(length=96), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_reads")),
        sa.UniqueConstraint(
            "tenant_id", "scope_key", "path", name="uq_file_reads_scope_path"
        ),
    )
    op.create_index(
        "ix_file_reads_run",
        "file_reads",
        ["tenant_id", "run_id", "path"],
        unique=False,
    )
    op.create_index(
        op.f("ix_file_reads_tenant_id"), "file_reads", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_file_reads_tenant_id"), table_name="file_reads")
    op.drop_index("ix_file_reads_run", table_name="file_reads")
    op.drop_table("file_reads")
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_column("bytes_written")
