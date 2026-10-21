"""交付物二进制与下载元数据(filename/content_type/content_bytes)

Revision ID: f7b2c9d41a08
Revises: c4d8e6f2a9b1
Create Date: 2026-10-21 23:55:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2c9d41a08"
down_revision: str | None = "c4d8e6f2a9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite 不支持裸 ALTER,统一走 batch(重建表)模式
    with op.batch_alter_table("workspace_artifacts") as batch_op:
        batch_op.add_column(sa.Column("filename", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("content_type", sa.String(length=128), nullable=True)
        )
        batch_op.add_column(sa.Column("content_bytes", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workspace_artifacts") as batch_op:
        batch_op.drop_column("content_bytes")
        batch_op.drop_column("content_type")
        batch_op.drop_column("filename")
