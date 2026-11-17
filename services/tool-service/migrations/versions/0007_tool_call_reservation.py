"""tool_calls 增加执行期预留(status/lease_until/cancel_requested/result_json)

Revision ID: 0007_tool_call_reservation
Revises: 0006_tool_call_idempotency
Create Date: 2026-11-17 23:10:00.000000

S1 的稳定键只能靠唯一约束在「提交期」兜并发:审计行是执行完才插的,同键请求
在「执行已经发生、行还没落库」的窗口里仍会各执行一次。这里给 tool_calls 加
执行期状态与租约——先插一行 status=running 的预留行再执行,同键请求立刻
409;进程崩溃留下的 running 行在租约过期后允许被抢占重试。

cancel_requested 供 orchestrator 取消在飞调用;result_json 留存成功信封,让
带稳定键的重放能回真实结果而不是一句「已执行过」。downgrade 只删本迁移加的
列与默认值。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_tool_call_reservation"
down_revision: str | None = "0006_tool_call_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default=sa.text("'succeeded'"),
            )
        )
        batch_op.add_column(
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "cancel_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("result_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as batch_op:
        batch_op.drop_column("result_json")
        batch_op.drop_column("cancel_requested")
        batch_op.drop_column("lease_until")
        batch_op.drop_column("status")
