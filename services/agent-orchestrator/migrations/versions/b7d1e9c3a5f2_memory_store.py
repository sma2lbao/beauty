"""memory store

Revision ID: b7d1e9c3a5f2
Revises: c8f2a5d9e1b4
Create Date: 2026-10-25 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d1e9c3a5f2"
down_revision: str | None = "c8f2a5d9e1b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建对话摘要表与长期记忆表(记忆能力一期)。"""
    op.create_table(
        "conversation_summaries",
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("covered_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("covered_messages", sa.Integer(), nullable=False),
        sa.Column("tokens_before", sa.Integer(), nullable=False),
        sa.Column("tokens_after", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_summaries")),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_id",
            name=op.f("uq_conversation_summaries_tenant_id"),
        ),
    )
    op.create_index(
        op.f("ix_conversation_summaries_tenant_id"),
        "conversation_summaries",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_summaries_conversation_id"),
        "conversation_summaries",
        ["conversation_id"],
        unique=False,
    )
    op.create_table(
        "memories",
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_model", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        op.f("ix_memories_tenant_id"), "memories", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_memories_workspace_id"),
        "memories",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    """回滚:先删记忆表,再删摘要表(与建表顺序相反)。"""
    op.drop_index(op.f("ix_memories_workspace_id"), table_name="memories")
    op.drop_index(op.f("ix_memories_tenant_id"), table_name="memories")
    op.drop_table("memories")
    op.drop_index(
        op.f("ix_conversation_summaries_conversation_id"),
        table_name="conversation_summaries",
    )
    op.drop_index(
        op.f("ix_conversation_summaries_tenant_id"),
        table_name="conversation_summaries",
    )
    op.drop_table("conversation_summaries")
