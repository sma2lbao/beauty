"""chat tool confirmations

Revision ID: a9c4e2f7b8d1
Revises: e7f3a1c9b5d2
Create Date: 2026-11-04 21:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c4e2f7b8d1"
down_revision: str | None = "e7f3a1c9b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建对话内写操作确认表(§6.7 就地确认)。

    pending 行由 chat 路径落库(WRITE 工具不执行),确认端点执行后回填
    result_text/status/message_id;tool_call_id 唯一约束保证同一次模型调用
    只生成一张卡片。
    """
    op.create_table(
        "chat_tool_confirmations",
        sa.Column("conversation_id", sa.String(length=32), nullable=False),
        sa.Column("message_id", sa.String(length=32), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("risk", sa.String(length=16), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
            name=op.f(
                "fk_chat_tool_confirmations_conversations_conversation_id"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name=op.f("fk_chat_tool_confirmations_chat_messages_message_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_tool_confirmations")),
        sa.UniqueConstraint(
            "tenant_id",
            "tool_call_id",
            name=op.f("uq_chat_tool_confirmations_tenant_id"),
        ),
    )
    op.create_index(
        op.f("ix_chat_tool_confirmations_tenant_id"),
        "chat_tool_confirmations",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_tool_confirmations_tenant_conversation",
        "chat_tool_confirmations",
        ["tenant_id", "conversation_id"],
        unique=False,
    )


def downgrade() -> None:
    """回滚:删确认表(索引随表一起删除,显式删便于审阅)。"""
    op.drop_index(
        "ix_chat_tool_confirmations_tenant_conversation",
        table_name="chat_tool_confirmations",
    )
    op.drop_index(
        op.f("ix_chat_tool_confirmations_tenant_id"),
        table_name="chat_tool_confirmations",
    )
    op.drop_table("chat_tool_confirmations")
