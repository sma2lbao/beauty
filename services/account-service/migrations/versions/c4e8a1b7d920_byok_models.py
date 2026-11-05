"""byok_keys.models:BYOK 自定义模型清单(M0-11 扩展)

Revision ID: c4e8a1b7d920
Revises: b9d5f2c8a1e4
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1b7d920"
down_revision: str | None = "b9d5f2c8a1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "byok_keys",
        sa.Column("models", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("byok_keys", "models")
