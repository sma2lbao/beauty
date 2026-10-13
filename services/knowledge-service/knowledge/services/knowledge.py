"""知识库业务逻辑:ingest(分块 + 向量化)、列表/详情/删除、向量检索。

数据访问经 `knowledge.repositories`(租户过滤与 404 语义在仓储声明);
阶段 A 检索在 Python 侧算余弦(取租户全部块),语料变大后换 pgvector,
本模块的读端契约不变。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument
from knowledge.repositories import (
    DocumentChunkRepository,
    KnowledgeDocumentRepository,
)
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
    documents = KnowledgeDocumentRepository(session)
    chunks_repo = DocumentChunkRepository(session)

    document = KnowledgeDocument(
        tenant_id=tenant_id,
        title=data.title,
        source=data.source,
        size_bytes=len(data.content.encode("utf-8")),
        chunk_count=len(chunks),
        embedding_model=embedder.model,
    )
    await documents.add(document)
    for ordinal, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
        await chunks_repo.add(
            DocumentChunk(
                tenant_id=tenant_id,
                document_id=document.id,
                ordinal=ordinal,
                content=content,
                embedding=vector,
            ),
            flush=False,
        )
    await session.flush()
    return document


async def list_documents(
    session: AsyncSession, tenant_id: str
) -> list[KnowledgeDocument]:
    return await KnowledgeDocumentRepository(session).list_scoped(
        tenant_id,
        order_by=[KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc()],
    )


async def require_document(
    session: AsyncSession, tenant_id: str, document_id: str
) -> KnowledgeDocument:
    return await KnowledgeDocumentRepository(session).require_for_tenant(
        tenant_id, document_id
    )


async def delete_document(
    session: AsyncSession, tenant_id: str, document_id: str
) -> None:
    """删除文档并连带清理分块(阶段 A 显式删除,不依赖外键级联)。"""
    documents = KnowledgeDocumentRepository(session)
    document = await documents.require_for_tenant(tenant_id, document_id)
    await DocumentChunkRepository(session).delete_for_document(tenant_id, document.id)
    await documents.delete(document)


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
    rows = await DocumentChunkRepository(session).searchable_rows(tenant_id)
    scored = [
        (chunk, title, cosine_similarity(query_vector, list(chunk.embedding or [])))
        for chunk, title in rows
    ]
    scored.sort(key=lambda item: (-item[2], item[0].ordinal, item[0].id))
    return scored[:limit]
