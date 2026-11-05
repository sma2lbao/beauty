"""tenancy to account:workspace-service 列/约束/索引改名

Revision ID: 3e80f7d1d5d7
Revises: e5a7c9b1d3f4
Create Date: 2026-11-05 23:20:00.000000

拆掉组织/套餐产品层后的账号边界(account_id = users.id):tenant_id → account_id,并把依赖旧列名的
唯一约束/索引一起改名(只改名,不删数据、不改语义)。

- workspaces: ix_workspaces_tenant_id → ix_workspaces_account_id,
  uq_workspaces_tenant_id → uq_workspaces_account_id("同账号内名称唯一")
- workspace_artifacts: ix_workspace_artifacts_tenant_id → ..._account_id
- sandboxes: ix_sandboxes_tenant_id → ix_sandboxes_account_id,
  uq_sandboxes_tenant_scope → uq_sandboxes_account_scope(部分唯一索引)

SQLite 不支持裸 ALTER,列改名与约束改名统一走 batch_alter_table(重建表);
Postgres 上 batch 模式退化为逐条 ALTER。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e80f7d1d5d7"
down_revision: str | None = "e5a7c9b1d3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPE_KEY_NOT_NULL = sa.text("scope_key IS NOT NULL")
OLD_SCOPE_INDEX = "uq_sandboxes_tenant_scope"
NEW_SCOPE_INDEX = "uq_sandboxes_account_scope"


def upgrade() -> None:
    op.drop_index("ix_workspaces_tenant_id", table_name="workspaces")
    op.drop_index("ix_workspace_artifacts_tenant_id", table_name="workspace_artifacts")
    op.drop_index("ix_sandboxes_tenant_id", table_name="sandboxes")
    op.drop_index(OLD_SCOPE_INDEX, table_name="sandboxes")

    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_constraint("uq_workspaces_tenant_id", type_="unique")
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("workspace_artifacts") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("sandboxes") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    op.create_index("ix_workspaces_account_id", "workspaces", ["account_id"], unique=False)
    op.create_index(
        "ix_workspace_artifacts_account_id",
        "workspace_artifacts",
        ["account_id"],
        unique=False,
    )
    op.create_index("ix_sandboxes_account_id", "sandboxes", ["account_id"], unique=False)

    # 唯一约束必须落在 batch 里:SQLite 不支持 ALTER TABLE ADD CONSTRAINT
    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.create_unique_constraint(
            "uq_workspaces_account_id", ["account_id", "name"]
        )

    op.create_index(
        NEW_SCOPE_INDEX,
        "sandboxes",
        ["account_id", "scope_key"],
        unique=True,
        sqlite_where=SCOPE_KEY_NOT_NULL,
        postgresql_where=SCOPE_KEY_NOT_NULL,
    )


def downgrade() -> None:
    op.drop_index(NEW_SCOPE_INDEX, table_name="sandboxes")

    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.drop_constraint("uq_workspaces_account_id", type_="unique")

    op.drop_index("ix_sandboxes_account_id", table_name="sandboxes")
    op.drop_index("ix_workspace_artifacts_account_id", table_name="workspace_artifacts")
    op.drop_index("ix_workspaces_account_id", table_name="workspaces")

    with op.batch_alter_table("sandboxes") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("workspace_artifacts") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("workspaces") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_workspaces_tenant_id", ["tenant_id", "name"]
        )

    op.create_index("ix_workspaces_tenant_id", "workspaces", ["tenant_id"], unique=False)
    op.create_index(
        "ix_workspace_artifacts_tenant_id",
        "workspace_artifacts",
        ["tenant_id"],
        unique=False,
    )
    op.create_index("ix_sandboxes_tenant_id", "sandboxes", ["tenant_id"], unique=False)
    op.create_index(
        OLD_SCOPE_INDEX,
        "sandboxes",
        ["tenant_id", "scope_key"],
        unique=True,
        sqlite_where=SCOPE_KEY_NOT_NULL,
        postgresql_where=SCOPE_KEY_NOT_NULL,
    )
