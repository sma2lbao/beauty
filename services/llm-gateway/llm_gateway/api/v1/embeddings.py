"""embeddings 端点:内部服务的向量化统一从这里出去(架构 §5 唯一出口)。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from llm_gateway.core.config import Settings
from llm_gateway.schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from llm_gateway.services import embeddings as embeddings_service

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.post("", response_model=EmbeddingResponse)
async def create_embeddings(
    body: EmbeddingRequest, request: Request
) -> EmbeddingResponse:
    settings: Settings = request.app.state.settings
    return await embeddings_service.embed(settings, body)
