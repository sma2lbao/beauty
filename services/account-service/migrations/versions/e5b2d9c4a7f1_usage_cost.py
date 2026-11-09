"""usage_events:成本归因(P0-1)

网关按价目表估算的美元成本与 key 来源落 usage_events,用量页才能显示金额。
- cost_usd 可空:未知模型/BYOK 自定义模型未计价,空 ≠ 0 元;
- billing_source 区分平台 key 与用户自带 key。

Revision ID: e5b2d9c4a7f1
Revises: 60b129ed3713
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b2d9c4a7f1"
down_revision: str | None = "60b129ed3713"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_events", sa.Column("cost_usd", sa.Numeric(12, 6), nullable=True)
    )
    op.add_column(
        "usage_events",
        sa.Column(
            "billing_source",
            sa.String(length=16),
            nullable=False,
            server_default="platform",
        ),
    )


def downgrade() -> None:
    op.drop_column("usage_events", "billing_source")
    op.drop_column("usage_events", "cost_usd")
