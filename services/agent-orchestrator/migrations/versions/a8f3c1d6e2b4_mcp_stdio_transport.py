"""mcp_servers: stdio 传输字段(P2 沙箱内 MCP)

给既有行补 server_default:transport/http、runtime/python、args/[],
command/encrypted_env/image 可空。url 列保持非空(stdio 存空字符串)。

Revision ID: a8f3c1d6e2b4
Revises: f2c6a9d4e8b1
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8f3c1d6e2b4"
down_revision: str | None = "f2c6a9d4e8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column(
            "transport",
            sa.String(length=16),
            nullable=False,
            server_default="http",
        ),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("command", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("args", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "mcp_servers",
        sa.Column(
            "runtime",
            sa.String(length=16),
            nullable=False,
            server_default="python",
        ),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("image", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "mcp_servers",
        sa.Column("encrypted_env", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    for column in ("encrypted_env", "image", "runtime", "args", "command", "transport"):
        op.drop_column("mcp_servers", column)
