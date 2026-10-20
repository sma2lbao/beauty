"""mcp_servers: auto_approve 免确认执行标记

给既有行补 server_default false(默认仍逐次确认)。置 True 的 server 其工具
在 chat 路径可直接执行、run 路径也不再进确认闸——docs/architecture.md §6.4
"操作策略是配置,不是硬编码"。

Revision ID: c3a7e9d1b4f6
Revises: e4c8b1f7a2d9
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3a7e9d1b4f6"
down_revision: str | None = "e4c8b1f7a2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column(
            "auto_approve",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "auto_approve")
