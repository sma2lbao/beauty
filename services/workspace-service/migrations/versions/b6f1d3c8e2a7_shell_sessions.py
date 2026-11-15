"""持久 shell 会话目录(跨实例所有权登记)

Revision ID: b6f1d3c8e2a7
Revises: 3e80f7d1d5d7
Create Date: 2026-11-15 23:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6f1d3c8e2a7"
down_revision: str | None = "3e80f7d1d5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shell_sessions",
        sa.Column("sandbox_id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("runtime_name", sa.String(length=128), nullable=False),
        sa.Column("provider_pid", sa.Integer(), nullable=True),
        sa.Column("cwd", sa.String(length=512), nullable=False),
        sa.Column("cols", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("tty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_instance", sa.String(length=128), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shell_sessions")),
        sa.UniqueConstraint(
            "account_id",
            "sandbox_id",
            "session_id",
            name="uq_shell_sessions_account_sandbox_session",
        ),
    )
    op.create_index(
        op.f("ix_shell_sessions_account_id"),
        "shell_sessions",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_shell_sessions_sandbox_id"),
        "shell_sessions",
        ["sandbox_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_shell_sessions_status"), "shell_sessions", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_shell_sessions_owner_instance"),
        "shell_sessions",
        ["owner_instance"],
        unique=False,
    )
    op.create_index(
        "ix_shell_sessions_lease",
        "shell_sessions",
        ["status", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shell_sessions_lease", table_name="shell_sessions")
    op.drop_index(
        op.f("ix_shell_sessions_owner_instance"), table_name="shell_sessions"
    )
    op.drop_index(op.f("ix_shell_sessions_status"), table_name="shell_sessions")
    op.drop_index(op.f("ix_shell_sessions_sandbox_id"), table_name="shell_sessions")
    op.drop_index(op.f("ix_shell_sessions_account_id"), table_name="shell_sessions")
    op.drop_table("shell_sessions")
