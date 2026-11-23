"""分块向量改存 pgvector + HNSW / pg_trgm 索引

K1(见 docs/design/knowledge-and-memory-upgrade.md §5.1):改造前
`document_chunks.embedding` 是 JSON 列,检索把本账号全部块搬回进程内算余弦,
语料一大就线性变慢。这里换成 `vector(1024)` 并建 HNSW 索引;关键词侧加
`pg_trgm` 的 GIN 索引,给混合检索的字面粗筛用。

维度变化:本地哈希提供方从 256 维改成 1024 维(与默认网关模型 bge-m3 对齐,
否则 local/gateway 切换时列类型要跟着变)。旧向量无法复用,迁移把维度不等于
1024 的向量置 NULL 并**保留正文**:这些块检索时被跳过,需要重新入库
(或等 K5 的 `/knowledge/reembed`)才能恢复向量检索;维度正好是 1024 的历史数据
(此前用 gateway 嵌入的)直接转换。

SQLite(测试/离线覆盖库)没有 vector/pg_trgm:直接跳过。测试库由
`create_all` 建表,列类型本来就是模型声明的同一个自定义类型,不需要额外动作。

Revision ID: b7d4c2a19f30
Revises: 1cbf87af2f2b
Create Date: 2026-11-22 23:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

from knowledge.core.vectors import EMBEDDING_DIM

revision: str = "b7d4c2a19f30"
down_revision: str | None = "1cbf87af2f2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HNSW_INDEX = "ix_document_chunks_embedding_hnsw"
TRGM_INDEX = "ix_document_chunks_content_trgm"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":  # pragma: no cover - 迁移只在 PG 上跑
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column(
        "document_chunks",
        sa.Column(
            "embedding_vec",
            VECTOR(EMBEDDING_DIM),
            nullable=True,
        ),
    )
    # JSON 数组 → vector:按原顺序取元素,只在维度正好匹配时转换(否则留 NULL)
    op.execute(
        f"""
        UPDATE document_chunks AS c
        SET embedding_vec = (
            SELECT array_agg(t.value::real ORDER BY t.ordinality)::vector
            FROM jsonb_array_elements_text(c.embedding::jsonb)
                 WITH ORDINALITY AS t(value, ordinality)
        )
        WHERE c.embedding IS NOT NULL
          AND jsonb_typeof(c.embedding::jsonb) = 'array'
          AND jsonb_array_length(c.embedding::jsonb) = {EMBEDDING_DIM}
        """
    )
    op.drop_column("document_chunks", "embedding")
    op.alter_column("document_chunks", "embedding_vec", new_column_name="embedding")

    op.create_index(
        HNSW_INDEX,
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        TRGM_INDEX,
        "document_chunks",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":  # pragma: no cover - 迁移只在 PG 上跑
        return

    op.drop_index(TRGM_INDEX, table_name="document_chunks")
    op.drop_index(HNSW_INDEX, table_name="document_chunks")

    op.add_column(
        "document_chunks", sa.Column("embedding_json", sa.JSON(), nullable=True)
    )
    op.execute(
        "UPDATE document_chunks SET embedding_json = to_jsonb(embedding::real[]) "
        "WHERE embedding IS NOT NULL"
    )
    op.drop_column("document_chunks", "embedding")
    op.alter_column("document_chunks", "embedding_json", new_column_name="embedding")
    # 降级后列允许 NULL(迁移期间被置空的向量没有原值可还原),重新入库即可补齐
