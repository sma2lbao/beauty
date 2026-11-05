"""tenancy to account:knowledge-service 列/索引改名

Revision ID: 1cbf87af2f2b
Revises: a60c8a781e43
Create Date: 2026-11-05 23:20:00.000000

拆掉组织/套餐产品层后的账号边界(account_id = users.id):knowledge_documents / document_chunks 的
tenant_id → account_id,索引同步改名;只改名,不删数据、不改约束语义。

SQLite 不支持裸 ALTER,列改名统一走 batch_alter_table(重建表);
Postgres 上 batch 模式退化为逐条 ALTER。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1cbf87af2f2b"
down_revision: str | None = "a60c8a781e43"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_knowledge_documents_tenant_id", table_name="knowledge_documents")
    op.drop_index("ix_document_chunks_tenant_id", table_name="document_chunks")

    with op.batch_alter_table("knowledge_documents") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            new_column_name="account_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    op.create_index(
        "ix_knowledge_documents_account_id",
        "knowledge_documents",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_account_id",
        "document_chunks",
        ["account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_account_id", table_name="document_chunks")
    op.drop_index(
        "ix_knowledge_documents_account_id", table_name="knowledge_documents"
    )

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    with op.batch_alter_table("knowledge_documents") as batch_op:
        batch_op.alter_column(
            "account_id",
            new_column_name="tenant_id",
            existing_type=sa.String(length=32),
            existing_nullable=False,
        )

    op.create_index(
        "ix_knowledge_documents_tenant_id",
        "knowledge_documents",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_tenant_id", "document_chunks", ["tenant_id"], unique=False
    )
