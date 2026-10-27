"""run_todos:runs.todos 任务清单快照列(M4)

Revision ID: b9e4d1f7c2a8
Revises: b7d1e9c3a5f2
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4d1f7c2a8"
down_revision: str | None = "b7d1e9c3a5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 非空列加到已有表上要给存量行默认值:历史 run 没有清单,空数组即可。
    # 保留 server_default 让迁移在 SQLite/PG 上都能一次走完(SQLite 不支持
    # ALTER COLUMN 去默认值);ORM 写入始终显式给值,与模型定义不冲突。
    op.add_column(
        "runs",
        sa.Column(
            "todos",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "todos")
