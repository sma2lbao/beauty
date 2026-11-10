"""审批策略参数条件 + 会话级授权

P1-A:让审批规则能按**工具参数**区分(如"只允许 title 以「周报」开头的笔记"),
并支持对话里用户选择"本会话内信任该工具"(grant=session)。

- approval_policies 加 arg_name/arg_match/arg_value 三列,非空、默认空串
  (空串 = 无条件规则;不用 NULL,避免 PG/SQLite 的唯一约束 NULL 语义差异);
- 唯一约束从 (account_id, tool) 扩成 (account_id, tool, arg_name, arg_match, arg_value);
- 新表 conversation_approval_grants:会话级授权,动作恒为 auto,删会话时同事务清理。

SQLite 走 batch 模式重建表,Postgres 上 batch 退化为原生 ALTER;downgrade 可逆
(多参数规则退化成同 (account_id, tool) 多行时会撞旧唯一约束,需先清理)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2b7c4a9d1f3"
down_revision: str | None = "a9d3e5c7b2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approval_policies") as batch:
        batch.add_column(
            sa.Column(
                "arg_name", sa.String(length=64), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column(
                "arg_match", sa.String(length=16), nullable=False, server_default=""
            )
        )
        batch.add_column(
            sa.Column(
                "arg_value", sa.String(length=512), nullable=False, server_default=""
            )
        )
        batch.drop_constraint("uq_approval_policies_account_tool", type_="unique")
        batch.create_unique_constraint(
            "uq_approval_policies_account_tool_args",
            ["account_id", "tool", "arg_name", "arg_match", "arg_value"],
        )

    op.create_table(
        "conversation_approval_grants",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("tool", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "conversation_id",
            "tool",
            name="uq_conversation_grants_account_conversation_tool",
        ),
    )
    op.create_index(
        "ix_conversation_approval_grants_account_id",
        "conversation_approval_grants",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    # 多个参数条件退化成同 (account_id, tool) 多行时会撞旧唯一约束;
    # 有损迁移,回退前需先清理参数规则(与 b6f1c8d92a47 同类约定)。
    op.drop_index(
        "ix_conversation_approval_grants_account_id",
        table_name="conversation_approval_grants",
    )
    op.drop_table("conversation_approval_grants")

    with op.batch_alter_table("approval_policies") as batch:
        batch.drop_constraint("uq_approval_policies_account_tool_args", type_="unique")
        batch.create_unique_constraint(
            "uq_approval_policies_account_tool", ["account_id", "tool"]
        )
        batch.drop_column("arg_value")
        batch.drop_column("arg_match")
        batch.drop_column("arg_name")
