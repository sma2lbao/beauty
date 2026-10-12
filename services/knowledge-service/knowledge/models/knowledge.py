"""知识库 ORM:文档与分块(租户隔离,向量先存 JSON 列)。

阶段 A:向量以 JSON list 存 `document_chunks.embedding`,检索在 Python 侧算余弦;
阶段 B 换 pgvector/专用向量库时,本表只留引用,读端契约不变。
"""

from __future__ import annotations

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column


class KnowledgeDocument(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """一篇被登记的文档(正文只保留分块,不再存全文)。"""

    __tablename__ = "knowledge_documents"

    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(32), default="manual")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(64))


class DocumentChunk(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """文档分块 + 向量(ordinal 从 0 递增,便于回放原文顺序)。"""

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)

    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
