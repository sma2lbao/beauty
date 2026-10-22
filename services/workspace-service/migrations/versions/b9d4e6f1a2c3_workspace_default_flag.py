"""工作区默认标记(is_default)

Revision ID: b9d4e6f1a2c3
Revises: f7b2c9d41a08
Create Date: 2026-10-22 21:50:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9d4e6f1a2c3"
down_revision: str | None = "f7b2c9d41a08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移前"自动创建"的默认工作区描述(旧数据只有这个判据);改名后仍应被认作默认
LEGACY_DEFAULT_DESCRIPTION = "自动创建:租户默认工作区"


def upgrade() -> None:
    # SQLite 不支持裸 ALTER,统一走 batch(重建表)模式
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    # 既有行是早期自动创建的默认工作区(默认标记缺失),必须补上 true:
    # 否则首次 GET /v1/workspaces/default 会因为按 is_default 找不到而重复建档。
    op.execute(
        sa.text(
            "UPDATE workspaces SET is_default = true "
            "WHERE description = :legacy_description"
        ).bindparams(legacy_description=LEGACY_DEFAULT_DESCRIPTION)
    )


def downgrade() -> None:
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_column("is_default")
