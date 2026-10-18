"""attachments:extracted_text/extraction_error(M3 附件解析)

Revision ID: f2c6a9d4e8b1
Revises: d7f3b2a9c4e1
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2c6a9d4e8b1"
down_revision: str | None = "d7f3b2a9c4e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments", sa.Column("extracted_text", sa.Text(), nullable=True)
    )
    op.add_column(
        "attachments",
        sa.Column("extraction_error", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachments", "extraction_error")
    op.drop_column("attachments", "extracted_text")
