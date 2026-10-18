"""attachments:会话附件本地最小版(内容存 DB)

Revision ID: e2b5c9d3f7a1
Revises: c4a7d1e9f2b8
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b5c9d3f7a1"
down_revision: str | None = "c4a7d1e9f2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=32), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=32), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_attachments_conversations_conversation_id"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_attachments_chat_messages_message_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachments")),
    )
    op.create_index(
        op.f("ix_attachments_tenant_id"),
        "attachments",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attachments_conversation_id"),
        "attachments",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_attachments_message_id"),
        "attachments",
        ["message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_attachments_message_id"), table_name="attachments")
    op.drop_index(op.f("ix_attachments_conversation_id"), table_name="attachments")
    op.drop_index(op.f("ix_attachments_tenant_id"), table_name="attachments")
    op.drop_table("attachments")
