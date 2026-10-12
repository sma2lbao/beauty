"""知识库端点:登记/列表/详情/删除 + 向量检索。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from knowledge.api.deps import (
    get_embedder,
    get_session,
    get_settings,
    get_tenant_id,
)
from knowledge.core.config import Settings
from knowledge.schemas.knowledge import (
    DocumentCreate,
    DocumentRead,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from knowledge.services import knowledge as knowledge_service
from knowledge.services.embeddings import EmbeddingProvider

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
Embedder = Annotated[EmbeddingProvider, Depends(get_embedder)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/documents", response_model=DocumentRead, status_code=status.HTTP_201_CREATED
)
async def create_document(
    body: DocumentCreate,
    session: DBSession,
    tenant_id: TenantId,
    embedder: Embedder,
    settings: SettingsDep,
) -> DocumentRead:
    """登记文档:分块 + 向量化(§9)。"""
    document = await knowledge_service.ingest_document(
        session,
        tenant_id,
        body,
        embedder,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    return DocumentRead.model_validate(document)


@router.get("/documents", response_model=list[DocumentRead])
async def list_documents(session: DBSession, tenant_id: TenantId) -> list[DocumentRead]:
    documents = await knowledge_service.list_documents(session, tenant_id)
    return [DocumentRead.model_validate(document) for document in documents]


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str, session: DBSession, tenant_id: TenantId
) -> DocumentRead:
    document = await knowledge_service.require_document(session, tenant_id, document_id)
    return DocumentRead.model_validate(document)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, session: DBSession, tenant_id: TenantId
) -> Response:
    """删除文档及其分块(租户内)。"""
    await knowledge_service.delete_document(session, tenant_id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    session: DBSession,
    tenant_id: TenantId,
    embedder: Embedder,
    settings: SettingsDep,
) -> SearchResponse:
    """向量检索:返回带分数的命中块(§9);分数依据随 provider/model 一并返回。"""
    limit = body.limit or settings.search_default_limit
    limit = max(1, min(limit, settings.search_max_limit))
    hits = await knowledge_service.search(
        session, tenant_id, body.query, limit, embedder
    )
    return SearchResponse(
        query=body.query,
        provider=embedder.name,
        model=embedder.model,
        hits=[
            SearchHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                title=title,
                ordinal=chunk.ordinal,
                content=chunk.content,
                score=round(score, 6),
            )
            for chunk, title, score in hits
        ],
    )
