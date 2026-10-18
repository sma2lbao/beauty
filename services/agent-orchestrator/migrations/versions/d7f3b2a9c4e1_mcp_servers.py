"""mcp_servers:MCP Client 注册表(M1)

Revision ID: d7f3b2a9c4e1
Revises: c5e8a1f4b2d7
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3b2a9c4e1"
down_revision: str | None = "c5e8a1f4b2d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("encrypted_token", sa.Text(), nullable=True),
        sa.Column("token_hint", sa.String(length=16), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("tools_allowlist", sa.JSON(), nullable=False),
        sa.Column("tools_cache", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mcp_servers")),
        sa.UniqueConstraint(
            "tenant_id", "name", name=op.f("uq_mcp_servers_tenant_id")
        ),
    )
    op.create_index(
        op.f("ix_mcp_servers_tenant_id"), "mcp_servers", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mcp_servers_tenant_id"), table_name="mcp_servers")
    op.drop_table("mcp_servers")
