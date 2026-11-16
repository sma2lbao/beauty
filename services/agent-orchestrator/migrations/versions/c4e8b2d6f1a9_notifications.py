"""notifications

Revision ID: c4e8b2d6f1a9
Revises: a1d4c7e9b2f6
Create Date: 2026-11-16 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e8b2d6f1a9"
down_revision: str | None = "a1d4c7e9b2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建站内通知表(关闭页面也保留未读;幂等写入靠唯一约束)。"""
    op.create_table(
        "notifications",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tone", sa.String(length=16), nullable=False),
        sa.Column("to_path", sa.String(length=200), nullable=True),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("conversation_id", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("account_id", sa.String(length=32), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint(
            "account_id",
            "kind",
            "dedupe_key",
            name="uq_notifications_account_kind_dedupe",
        ),
    )
    op.create_index(
        op.f("ix_notifications_account_id"),
        "notifications",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notifications_run_id"),
        "notifications",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_account_created_at",
        "notifications",
        ["account_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_account_read_at",
        "notifications",
        ["account_id", "read_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除站内通知表。"""
    op.drop_index("ix_notifications_account_read_at", table_name="notifications")
    op.drop_index("ix_notifications_account_created_at", table_name="notifications")
    op.drop_index(op.f("ix_notifications_run_id"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_account_id"), table_name="notifications")
    op.drop_table("notifications")
