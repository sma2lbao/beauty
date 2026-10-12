"""source 放宽到 64

背景:知识库的 `source` 是来源/溯源标识,原来的 32 位放不下
`run:<32 位 run id>`(36 字符)——orchestrator 的"交付物自动入库"消费者
因此拿 422。放宽到 64,允许这类溯源串。

兼容性:只是放宽长度上限(向后兼容);sqlite 用 batch 重建表,
Postgres 直接 ALTER COLUMN TYPE。

Revision ID: a60c8a781e43
Revises: e76b56f6ec7c
Create Date: 2026-10-13 07:30:15.368838

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a60c8a781e43"
down_revision: Union[str, None] = "e76b56f6ec7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.alter_column(
            "source",
            existing_type=sa.String(32),
            type_=sa.String(64),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.alter_column(
            "source",
            existing_type=sa.String(64),
            type_=sa.String(32),
            existing_nullable=False,
        )
