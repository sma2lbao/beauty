"""知识库端点:登记/列表/详情/删除 + 向量检索。"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.api.deps import (
    get_account_id,
    get_embedder,
    get_reranker,
    get_session,
    get_settings,
)
from knowledge.core.config import Settings
from knowledge.schemas.knowledge import (
    DocumentContent,
    DocumentCreate,
    DocumentRead,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from knowledge.services import knowledge as knowledge_service
from knowledge.services.embeddings import EmbeddingProvider
from knowledge.services.rerank import Reranker

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]
Embedder = Annotated[EmbeddingProvider, Depends(get_embedder)]
RerankerDep = Annotated[Reranker | None, Depends(get_reranker)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
async def create_document(
    body: DocumentCreate,
    session: DBSession,
    account_id: AccountId,
    embedder: Embedder,
    settings: SettingsDep,
) -> DocumentRead:
    """登记文档:分块 + 向量化(§9)。"""
    document = await knowledge_service.ingest_document(
        session,
        account_id,
        body,
        embedder,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    return DocumentRead.model_validate(document)


@router.get("/documents", response_model=list[DocumentRead])
async def list_documents(
    session: DBSession, account_id: AccountId
) -> list[DocumentRead]:
    documents = await knowledge_service.list_documents(session, account_id)
    return [DocumentRead.model_validate(document) for document in documents]


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str, session: DBSession, account_id: AccountId
) -> DocumentRead:
    document = await knowledge_service.require_document(
        session, account_id, document_id
    )
    return DocumentRead.model_validate(document)


@router.get("/documents/{document_id}/content", response_model=DocumentContent)
async def get_document_content(
    document_id: str,
    session: DBSession,
    account_id: AccountId,
    max_chars: Annotated[int, Query(ge=500, le=20_000)] = 8000,
) -> DocumentContent:
    """取文档可引用正文(分块按 ordinal 拼接,可截断);供输入区 @ 引用。"""
    document, content, truncated = await knowledge_service.document_content(
        session, account_id, document_id, max_chars=max_chars
    )
    return DocumentContent(
        document_id=document.id,
        title=document.title,
        content=content,
        truncated=truncated,
        chunk_count=document.chunk_count,
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, session: DBSession, account_id: AccountId
) -> Response:
    """删除文档及其分块(账号内)。"""
    await knowledge_service.delete_document(session, account_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    session: DBSession,
    account_id: AccountId,
    embedder: Embedder,
    reranker: RerankerDep,
    settings: SettingsDep,
) -> SearchResponse:
    """检索:vector / keyword / hybrid 三选一(默认按配置,hybrid) → 可选重排。

    返回的 `mode`、`rerank` 与 `score` 必须一起看:三种模式下 score 的口径不同
    (余弦相似度 / 词法覆盖率 / RRF 融合分),重排生效时改为重排相关性分数,
    见 `schemas.knowledge`。
    """
    limit = body.limit or settings.search_default_limit
    limit = max(1, min(limit, settings.search_max_limit))
    started = time.perf_counter()
    hits, mode, rerank_status = await knowledge_service.search(
        session,
        account_id,
        body.query,
        limit,
        embedder,
        reranker=reranker,
        rerank_candidates=settings.rerank_candidates,
        mode=body.mode,
        default_mode=settings.search_default_mode,
        rrf_k=settings.rrf_k,
        candidate_limit=settings.search_candidate_limit,
        vector_weight=(
            body.vector_weight
            if body.vector_weight is not None
            else settings.search_vector_weight
        ),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return SearchResponse(
        query=body.query,
        provider=embedder.name,
        model=embedder.model,
        mode=mode,
        elapsed_ms=elapsed_ms,
        rerank=rerank_status,
        rerank_model=reranker.model if rerank_status is not None else None,
        hits=[
            SearchHit(
                chunk_id=hit.chunk.id,
                document_id=hit.chunk.document_id,
                title=hit.title,
                ordinal=hit.chunk.ordinal,
                content=hit.chunk.content,
                score=round(hit.score, 6),
            )
            for hit in hits
        ],
    )
