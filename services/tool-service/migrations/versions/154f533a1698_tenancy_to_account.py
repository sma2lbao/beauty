"""tenancy to account：tool-service 列/约束/索引改名。

拆掉多租户产品层后的账号边界（account_id = users.id）：tool_calls / file_reads 的
tenant_id → account_id，唯一约束与索引一并改名（只改名，不删数据、不改语义）。
SQLite 不支持裸 ALTER，列改名与约束改名统一走 batch_alter_table（重建表），
Postgres 上 batch 退化为逐条 ALTER。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "154f533a1698"
down_revision: str | None = "0004_tool_call_connection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCOUNT = sa.String(length=32)


def upgrade() -> None:
    op.drop_index("ix_tool_calls_tenant_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_run", table_name="tool_calls")
    op.drop_index("ix_tool_calls_tool", table_name="tool_calls")
    op.drop_index("ix_file_reads_tenant_id", table_name="file_reads")
    op.drop_index("ix_file_reads_run", table_name="file_reads")

    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_constraint("uq_tool_calls_tenant_call", type_="unique")
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("file_reads") as batch_op:
        batch_op.drop_constraint("uq_file_reads_scope_path", type_="unique")
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )

    op.create_index("ix_tool_calls_account_id", "tool_calls", ["account_id"])
    op.create_index(
        "ix_tool_calls_run",
        "tool_calls",
        ["account_id", "run_id", "created_at"],
    )
    op.create_index(
        "ix_tool_calls_tool",
        "tool_calls",
        ["account_id", "tool_name", "created_at"],
    )
    op.create_index("ix_file_reads_account_id", "file_reads", ["account_id"])
    op.create_index(
        "ix_file_reads_run",
        "file_reads",
        ["account_id", "run_id", "path"],
    )

    # 唯一约束必须落在 batch 里重建表：SQLite 不支持 ALTER TABLE ADD CONSTRAINT
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.create_unique_constraint(
            "uq_tool_calls_account_call", ["account_id", "tool_call_id"]
        )
    with op.batch_alter_table("file_reads") as batch_op:
        batch_op.create_unique_constraint(
            "uq_file_reads_scope_path", ["account_id", "scope_key", "path"]
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_constraint("uq_tool_calls_account_call", type_="unique")
    with op.batch_alter_table("file_reads") as batch_op:
        batch_op.drop_constraint("uq_file_reads_scope_path", type_="unique")

    op.drop_index("ix_tool_calls_account_id", table_name="tool_calls")
    op.drop_index("ix_tool_calls_run", table_name="tool_calls")
    op.drop_index("ix_tool_calls_tool", table_name="tool_calls")
    op.drop_index("ix_file_reads_account_id", table_name="file_reads")
    op.drop_index("ix_file_reads_run", table_name="file_reads")

    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )
    with op.batch_alter_table("file_reads") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=ACCOUNT,
            existing_nullable=False,
        )

    op.create_index("ix_tool_calls_tenant_id", "tool_calls", ["tenant_id"])
    op.create_index(
        "ix_tool_calls_run",
        "tool_calls",
        ["tenant_id", "run_id", "created_at"],
    )
    op.create_index(
        "ix_tool_calls_tool",
        "tool_calls",
        ["tenant_id", "tool_name", "created_at"],
    )
    op.create_index("ix_file_reads_tenant_id", "file_reads", ["tenant_id"])
    op.create_index(
        "ix_file_reads_run",
        "file_reads",
        ["tenant_id", "run_id", "path"],
    )

    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.create_unique_constraint(
            "uq_tool_calls_tenant_call", ["tenant_id", "tool_call_id"]
        )
    with op.batch_alter_table("file_reads") as batch_op:
        batch_op.create_unique_constraint(
            "uq_file_reads_scope_path", ["tenant_id", "scope_key", "path"]
        )
