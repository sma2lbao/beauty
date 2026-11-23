"""rerank 端点:知识库/记忆的重排统一从这里出去(架构 §5 唯一出口)。

与 completions / embeddings 一样要求内部鉴权,并按 X-Account-Id 解析 BYOK 密钥。
"""

from __future__ import annotations

from typing import Annotated

from common.security import ACCOUNT_HEADER
from fastapi import APIRouter, Depends, Header, Request

from llm_gateway.api.deps import require_internal_secret, resolve_byok_api_key
from llm_gateway.core.config import Settings
from llm_gateway.schemas.rerank import RerankRequest, RerankResponse
from llm_gateway.services import rerank as rerank_service

router = APIRouter(prefix="/rerank", tags=["rerank"])

AccountHeader = Annotated[str | None, Header(alias=ACCOUNT_HEADER)]


def _provider(settings: Settings, model: str | None) -> str:
    """BYOK 查询用的 provider(取模型串前缀;无前缀时不猜,回退平台 key)。"""
    resolved = str(model or settings.default_rerank_model)
    return resolved.split("/", 1)[0] if "/" in resolved else ""


@router.post(
    "",
    response_model=RerankResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def create_rerank(
    body: RerankRequest,
    request: Request,
    x_account_id: AccountHeader = None,
) -> RerankResponse:
    settings: Settings = request.app.state.settings
    api_key = await resolve_byok_api_key(
        request,
        provider=_provider(settings, body.model),
        account_id=x_account_id,
    )
    return await rerank_service.rerank(settings, body, api_key=api_key)
