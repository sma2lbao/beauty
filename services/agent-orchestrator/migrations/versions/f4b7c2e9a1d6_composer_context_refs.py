"""conversations/runs:结构化引用(context_refs)与显式连接器(connector_providers)

Revision ID: f4b7c2e9a1d6
Revises: c4e8b2d6f1a9
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b7c2e9a1d6"
down_revision: str | None = "c4e8b2d6f1a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # context_refs: [{kind,id,title}] 的只读快照;默认空数组 = 没引用任何资料
    op.add_column(
        "chat_messages",
        sa.Column(
            "context_refs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )
    # 显式引用的连接器 provider;空数组 = 不限制(沿用全部已连接工具)
    op.add_column(
        "chat_messages",
        sa.Column(
            "connector_providers",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "runs",
        sa.Column(
            "connector_providers",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "connector_providers")
    op.drop_column("chat_messages", "connector_providers")
    op.drop_column("chat_messages", "context_refs")
