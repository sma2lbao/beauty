"""embeddings 端点:内部服务的向量化统一从这里出去(架构 §5 唯一出口)。

与 completions 一样要求内部鉴权,并按 X-Tenant-Id 解析 BYOK 密钥。
"""

from __future__ import annotations

from typing import Annotated

from common.security import TENANT_HEADER
from fastapi import APIRouter, Depends, Header, Request

from llm_gateway.api.deps import require_internal_secret, resolve_byok_api_key
from llm_gateway.core.config import Settings
from llm_gateway.schemas.embeddings import EmbeddingRequest, EmbeddingResponse
from llm_gateway.services import embeddings as embeddings_service

router = APIRouter(prefix="/embeddings", tags=["embeddings"])

TenantHeader = Annotated[str | None, Header(alias=TENANT_HEADER)]


def _provider(settings: Settings, model: str | None) -> str:
    """BYOK 查询用的 provider(取模型串前缀;无前缀时不猜,回退平台 key)。"""
    resolved = str(model or settings.default_embedding_model)
    return resolved.split("/", 1)[0] if "/" in resolved else ""


@router.post(
    "",
    response_model=EmbeddingResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def create_embeddings(
    body: EmbeddingRequest,
    request: Request,
    x_tenant_id: TenantHeader = None,
) -> EmbeddingResponse:
    settings: Settings = request.app.state.settings
    api_key = await resolve_byok_api_key(
        request,
        provider=_provider(settings, body.model),
        tenant_id=x_tenant_id,
    )
    return await embeddings_service.embed(settings, body, api_key=api_key)
