"""tool_calls 增加 connection_id 审计列(C2.4)

Revision ID: 0004_tool_call_connection
Revises: 0003_tool_call_patches
Create Date: 2026-11-04 19:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_tool_call_connection"
down_revision: str | None = "0003_tool_call_patches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 连接器侧解析到的连接 id(UUIDMixin 的 32 位字符串);内置工具为 NULL。
    op.add_column(
        "tool_calls", sa.Column("connection_id", sa.String(length=32), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_column("connection_id")
