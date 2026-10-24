"""approval:租户级工具审批策略

Revision ID: c8f2a5d9e1b4
Revises: e7c2a4b8d1f3
Create Date: 2026-10-24

引入 P0-4 审批策略引擎:``auto``/``confirm``/``deny`` 三条覆盖规则,按工具名
(或 ``*``)声明。空表时行为与之前一致(风险等级决定要不要确认)。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f2a5d9e1b4"
down_revision: str | None = "e7c2a4b8d1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_policies",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("tenant_id", sa.String(length=32), nullable=False, index=True),
        sa.Column("tool", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "tool", name="uq_approval_policies_tenant_tool"
        ),
    )


def downgrade() -> None:
    op.drop_table("approval_policies")
