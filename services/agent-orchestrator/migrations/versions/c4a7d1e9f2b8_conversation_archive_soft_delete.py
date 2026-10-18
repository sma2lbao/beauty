"""conversations.archived_at / deleted_at(归档与软删除)

Revision ID: c4a7d1e9f2b8
Revises: b41c7e9a2d10
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7d1e9f2b8"
down_revision: str | None = "b41c7e9a2d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("archived_at")
