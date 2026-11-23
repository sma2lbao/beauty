"""知识库业务逻辑:ingest(分块 + 向量化)、列表/详情/删除、检索入口。

数据访问经 `knowledge.repositories`(账号过滤与 404 语义在仓储声明);
检索的具体编排(向量 / 关键词 / RRF 融合)在 `knowledge.services.search`,
本模块只负责 ingest 与文档级操作。
"""

from __future__ import annotations

from common.errors import UpstreamError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.models.knowledge import DocumentChunk, KnowledgeDocument
from knowledge.repositories import (
    DocumentChunkRepository,
    KnowledgeDocumentRepository,
)
from knowledge.schemas.knowledge import DocumentCreate
from knowledge.services import search as search_service
from knowledge.services.chunking import chunk_text
from knowledge.services.embeddings import EmbeddingProvider
from knowledge.services.rerank import Reranker, RerankStatus

log = get_logger("knowledge.search")


async def ingest_document(
    session: AsyncSession,
    account_id: str,
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
        account_id=account_id,
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
                account_id=account_id,
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
    session: AsyncSession, account_id: str
) -> list[KnowledgeDocument]:
    return await KnowledgeDocumentRepository(session).list_scoped(
        account_id,
        order_by=[KnowledgeDocument.created_at.desc(), KnowledgeDocument.id.desc()],
    )


async def require_document(
    session: AsyncSession, account_id: str, document_id: str
) -> KnowledgeDocument:
    return await KnowledgeDocumentRepository(session).require_for_account(
        account_id, document_id
    )


async def delete_document(
    session: AsyncSession, account_id: str, document_id: str
) -> None:
    """删除文档并连带清理分块(阶段 A 显式删除,不依赖外键级联)。"""
    documents = KnowledgeDocumentRepository(session)
    document = await documents.require_for_account(account_id, document_id)
    await DocumentChunkRepository(session).delete_for_document(account_id, document.id)
    await documents.delete(document)


async def document_content(
    session: AsyncSession,
    account_id: str,
    document_id: str,
    *,
    max_chars: int,
) -> tuple[KnowledgeDocument, str, bool]:
    """取文档可引用正文:分块按 ordinal 拼接,超出 max_chars 截断。

    返回 (文档, 正文, 是否截断)。正文只来自分块(与检索一致),不读 embedding。
    """
    document = await require_document(session, account_id, document_id)
    chunks = await DocumentChunkRepository(session).list_chunks(account_id, document.id)
    parts: list[str] = []
    used = 0
    truncated = False
    for chunk in chunks:
        piece = chunk.content
        separator = 2 if parts else 0  # "\n\n"
        remaining = max_chars - used - separator
        if remaining <= 0:
            truncated = True
            break
        if len(piece) > remaining:
            parts.append(piece[:remaining])
            truncated = True
            break
        parts.append(piece)
        used += separator + len(piece)
    return document, "\n\n".join(parts), truncated


async def search(
    session: AsyncSession,
    account_id: str,
    query: str,
    limit: int,
    embedder: EmbeddingProvider,
    *,
    reranker: Reranker | None = None,
    rerank_candidates: int = 20,
    mode: str | None = None,
    default_mode: str = search_service.HYBRID_MODE,
    rrf_k: int = 60,
    candidate_limit: int = 50,
    vector_weight: float = 0.5,
) -> tuple[list[search_service.Hit], str, RerankStatus | None]:
    """检索入口:向量 / 关键词 / 混合(RRF) → 可选重排,返回 (命中, 模式, 重排状态)。

    score 的含义随模式变化(余弦相似度 / 词法覆盖率 / RRF 融合分),重排生效时
    改为重排相关性分数(0~1);所以模式与重排状态都必须随结果返回。

    重排先粗排 `limit` 与 `rerank_candidates` 中较大的条数,再按重排分数截到
    `limit`;网关失败时**不抛错**,退回原顺序并把状态标成 skipped(重排是增益,
    不能变成检索的单点故障)。
    """
    pool_limit = max(limit, rerank_candidates) if reranker is not None else limit
    hits, resolved_mode = await search_service.search(
        session,
        account_id,
        query,
        pool_limit,
        embedder,
        mode=mode,
        default_mode=default_mode,
        rrf_k=rrf_k,
        candidate_limit=candidate_limit,
        vector_weight=vector_weight,
    )
    if reranker is None:
        return hits, resolved_mode, None
    if len(hits) <= 1:
        # 候选只有一条时重排没有信息量,还白白多一次外部调用
        return hits[:limit], resolved_mode, "skipped"
    try:
        scores = await reranker.rerank(query, [hit.chunk.content for hit in hits])
    except UpstreamError as exc:
        log.warning("knowledge_rerank_failed", code=exc.code, message=str(exc))
        return hits[:limit], resolved_mode, "skipped"
    if len(scores) != len(hits):
        log.warning("knowledge_rerank_mismatch", expected=len(hits), got=len(scores))
        return hits[:limit], resolved_mode, "skipped"
    order = sorted(
        range(len(hits)),
        key=lambda index: (
            -scores[index],
            hits[index].chunk.ordinal,
            hits[index].chunk.id,
        ),
    )
    reranked = [
        search_service.Hit(
            chunk=hits[index].chunk,
            title=hits[index].title,
            score=scores[index],
        )
        for index in order
    ]
    return reranked[:limit], resolved_mode, "applied"
