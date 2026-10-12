"""知识库业务逻辑:ingest(分块 + 向量化)、列表/详情/删除、向量检索。

阶段 A 检索在 Python 侧算余弦(取租户全部块);语料变大后换 pgvector/向量库,
本模块的读端契约不变。
"""

from __future__ import annotations

from common.errors import NotFoundError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument
from knowledge.schemas.knowledge import DocumentCreate
from knowledge.services.chunking import chunk_text
from knowledge.services.embeddings import EmbeddingProvider, cosine_similarity


async def ingest_document(
    session: AsyncSession,
    tenant_id: str,
    data: DocumentCreate,
    embedder: EmbeddingProvider,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> KnowledgeDocument:
    """登记文档:分块 → 向量化 → 落库(正文只保留在分块里)。"""
    chunks = chunk_text(data.content, size=chunk_size, overlap=chunk_overlap)
    vectors = await embedder.embed(chunks)
    document = KnowledgeDocument(
        tenant_id=tenant_id,
        title=data.title,
        source=data.source,
        size_bytes=len(data.content.encode("utf-8")),
        chunk_count=len(chunks),
        embedding_model=embedder.model,
    )
    session.add(document)
    await session.flush()
    for ordinal, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            DocumentChunk(
                tenant_id=tenant_id,
                document_id=document.id,
                ordinal=ordinal,
                content=content,
                embedding=vector,
            )
        )
    await session.flush()
    return document


async def list_documents(
    session: AsyncSession, tenant_id: str
) -> list[KnowledgeDocument]:
    rows = await session.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc())
    )
    return list(rows)


async def require_document(
    session: AsyncSession, tenant_id: str, document_id: str
) -> KnowledgeDocument:
    document = await session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
        )
    )
    if document is None:
        raise NotFoundError(
            "knowledge.document_not_found",
            "文档不存在",
            details={"document_id": document_id},
        )
    return document


async def delete_document(
    session: AsyncSession, tenant_id: str, document_id: str
) -> None:
    """删除文档并连带清理分块(阶段 A 显式删除,不依赖外键级联)。"""
    document = await require_document(session, tenant_id, document_id)
    await session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.document_id == document.id,
            DocumentChunk.tenant_id == tenant_id,
        )
    )
    await session.delete(document)
    await session.flush()


async def search(
    session: AsyncSession,
    tenant_id: str,
    query: str,
    limit: int,
    embedder: EmbeddingProvider,
) -> list[tuple[DocumentChunk, str, float]]:
    """向量检索:返回 (分块, 文档标题, 分数),分数降序、同分按 ordinal 稳定。"""
    query_vectors = await embedder.embed([query])
    query_vector = query_vectors[0] if query_vectors else []
    rows = await session.execute(
        select(DocumentChunk, KnowledgeDocument.title)
        .join(KnowledgeDocument, KnowledgeDocument.id == DocumentChunk.document_id)
        .where(DocumentChunk.tenant_id == tenant_id)
    )
    scored = [
        (
            chunk,
            title,
            cosine_similarity(query_vector, list(chunk.embedding or [])),
        )
        for chunk, title in rows.all()
    ]
    scored.sort(key=lambda item: (-item[2], item[0].ordinal, item[0].id))
    return scored[:limit]
