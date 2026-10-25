"""沙箱 scope_key:ensure 幂等键与租户内部分唯一索引(P0-05)

Revision ID: e5a7c9b1d3f4
Revises: b9d4e6f1a2c3
Create Date: 2026-10-25 15:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a7c9b1d3f4"
down_revision: str | None = "b9d4e6f1a2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 部分唯一索引的过滤条件:NULL(非 ensure 创建的沙箱)不参与唯一
SCOPE_KEY_NOT_NULL = sa.text("scope_key IS NOT NULL")
TENANT_SCOPE_INDEX = "uq_sandboxes_tenant_scope"


def upgrade() -> None:
    # SQLite 不支持裸 ALTER,统一走 batch(重建表)模式
    with op.batch_alter_table("sandboxes") as batch_op:
        batch_op.add_column(sa.Column("scope_key", sa.String(length=96), nullable=True))
        batch_op.create_index(
            op.f("ix_sandboxes_scope_key"), ["scope_key"], unique=False
        )
    # 租户内幂等键:同一 (tenant_id, scope_key) 只允许一行。用部分唯一索引
    # (而不是唯一约束)是为了排除 scope_key IS NULL 的普通创建路径。
    op.create_index(
        TENANT_SCOPE_INDEX,
        "sandboxes",
        ["tenant_id", "scope_key"],
        unique=True,
        sqlite_where=SCOPE_KEY_NOT_NULL,
        postgresql_where=SCOPE_KEY_NOT_NULL,
    )


def downgrade() -> None:
    op.drop_index(TENANT_SCOPE_INDEX, table_name="sandboxes")
    with op.batch_alter_table("sandboxes") as batch_op:
        batch_op.drop_index(op.f("ix_sandboxes_scope_key"))
        batch_op.drop_column("scope_key")
