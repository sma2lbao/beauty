"""工具调用审计表

Revision ID: 0001_tool_calls
Revises:
Create Date: 2026-10-25 15:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_tool_calls"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("conversation_id", sa.String(length=32), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.String(length=16), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("arguments_sha256", sa.String(length=64), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("result_chars", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("paths", sa.Text(), nullable=False),
        sa.Column("sha256_before", sa.String(length=64), nullable=True),
        sa.Column("sha256_after", sa.String(length=64), nullable=True),
        sa.Column("sandbox_id", sa.String(length=128), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.UniqueConstraint(
            "tenant_id", "tool_call_id", name="uq_tool_calls_tenant_call"
        ),
    )
    op.create_index(
        "ix_tool_calls_run",
        "tool_calls",
        ["tenant_id", "run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tool_calls_tool",
        "tool_calls",
        ["tenant_id", "tool_name", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tool_calls_tenant_id"), "tool_calls", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_tool_calls_tenant_id"), table_name="tool_calls")
    op.drop_index("ix_tool_calls_tool", table_name="tool_calls")
    op.drop_index("ix_tool_calls_run", table_name="tool_calls")
    op.drop_table("tool_calls")
