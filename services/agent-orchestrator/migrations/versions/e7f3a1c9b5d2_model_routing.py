"""model_routing:runs / conversations 的模型路由列(P1-G)

Revision ID: e7f3a1c9b5d2
Revises: d2c7f5b9a1e4
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7f3a1c9b5d2"
down_revision: str | None = "d2c7f5b9a1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 全部可空、不回填:历史行没有模型路由信息,为空即"走网关默认路由"。
    # runs.model 是**快照**语义(建 run 时确定,之后改会话模型不影响已建 run),
    # 值形如 "provider/model"(见 docs/design/model-routing.md §4)。
    op.add_column("runs", sa.Column("model", sa.String(length=120), nullable=True))
    op.add_column(
        "conversations", sa.Column("model", sa.String(length=120), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversations", "model")
    op.drop_column("runs", "model")
