"""知识库 ORM:文档与分块(账号隔离,向量存 pgvector 列)。

向量列是 `vector(1024)`(见 `knowledge.core.vectors`):Postgres 上可建 HNSW 索引,
近似近邻检索不再把整表搬回进程内算余弦。SQLite(测试/离线)把同一类型按 text 存取,
bind/result 处理器仍然读写 `list[float]`,不需要第二套模型。

分块向量允许为 NULL:换嵌入维度/模型后旧向量无法复用,迁移会把它们置空并留待
`/knowledge/reembed` 重建;检索侧跳过 NULL,正文端点不受影响。
"""

from __future__ import annotations

from database.base import AccountMixin, Base, TimestampMixin, UUIDMixin
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from knowledge.core.vectors import embedding_vector_type


class KnowledgeDocument(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """一篇被登记的文档(正文只保留分块,不再存全文)。"""

    __tablename__ = "knowledge_documents"

    title: Mapped[str] = mapped_column(String(200))
    # 来源标识:允许 run:<32 位 id> 这类溯源串(64 足够)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(64))


class DocumentChunk(UUIDMixin, AccountMixin, TimestampMixin, Base):
    """文档分块 + 向量(ordinal 从 0 递增,便于回放原文顺序)。"""

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)

    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(
        embedding_vector_type(), nullable=True
    )
