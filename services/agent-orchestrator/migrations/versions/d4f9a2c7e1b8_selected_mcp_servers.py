"""conversations/runs:显式引用的 MCP server(对话页 MCP 引用)

Revision ID: d4f9a2c7e1b8
Revises: b8d2f4a6c9e3
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f9a2c7e1b8"
down_revision: str | None = "b8d2f4a6c9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 默认空数组 = 不限制(沿用"全部已启用 MCP server"的既有行为)
    op.add_column(
        "chat_messages",
        sa.Column(
            "mcp_server_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "mcp_server_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "mcp_server_ids")
    op.drop_column("chat_messages", "mcp_server_ids")
