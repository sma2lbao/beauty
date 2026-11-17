"""tool_calls 增加稳定幂等键(S1)

Revision ID: 0006_tool_call_idempotency
Revises: 0005_background_jobs
Create Date: 2026-11-17 21:30:00.000000

崩溃恢复后模型会重新生成 tool_call_id,服务端原先只能按它去重,于是同一次
写操作会被重复执行。orchestrator 现在额外传一个"run + 节点 + 工具 + 规范化
参数"算出的稳定键,这里落列并加账号内唯一约束。NULL 互不冲突,旧写入不受
影响;downgrade 删约束与列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_tool_call_idempotency"
down_revision: str | None = "0005_background_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.add_column(
            sa.Column("idempotency_key", sa.String(length=96), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_tool_calls_account_idempotency",
            ["account_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_constraint(
            "uq_tool_calls_account_idempotency", type_="unique"
        )
        batch_op.drop_column("idempotency_key")
