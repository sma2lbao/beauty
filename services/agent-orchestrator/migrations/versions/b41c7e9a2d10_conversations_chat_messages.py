"""conversations / chat_messages 表与 runs.conversation_id

Revision ID: b41c7e9a2d10
Revises: 7a432a3820a9
Create Date: 2026-10-15 21:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b41c7e9a2d10'
down_revision: Union[str, None] = '7a432a3820a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'conversations',
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.String(length=32), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations')),
    )
    op.create_index(
        op.f('ix_conversations_tenant_id'), 'conversations', ['tenant_id'], unique=False
    )
    op.create_table(
        'chat_messages',
        sa.Column('conversation_id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=True),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('tenant_id', sa.String(length=32), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['conversation_id'],
            ['conversations.id'],
            name=op.f('fk_chat_messages_conversations_conversation_id'),
        ),
        sa.ForeignKeyConstraint(
            ['run_id'], ['runs.id'], name=op.f('fk_chat_messages_runs_run_id')
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages')),
    )
    op.create_index(
        op.f('ix_chat_messages_conversation_id'),
        'chat_messages',
        ['conversation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_chat_messages_run_id'), 'chat_messages', ['run_id'], unique=False
    )
    op.create_index(
        op.f('ix_chat_messages_tenant_id'), 'chat_messages', ['tenant_id'], unique=False
    )
    # 弱引用(同 runs.parent_run_id):SQLite 的 ALTER TABLE 无法添加外键约束,
    # 模型侧同样不声明 FK,保持迁移与 ORM 一致(chat_messages 是新建表,带 FK)
    op.add_column(
        'runs',
        sa.Column('conversation_id', sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f('ix_runs_conversation_id'), 'runs', ['conversation_id'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_runs_conversation_id'), table_name='runs')
    op.drop_column('runs', 'conversation_id')
    op.drop_index(op.f('ix_chat_messages_tenant_id'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_run_id'), table_name='chat_messages')
    op.drop_index(
        op.f('ix_chat_messages_conversation_id'), table_name='chat_messages'
    )
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_conversations_tenant_id'), table_name='conversations')
    op.drop_table('conversations')
