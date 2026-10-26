"""completions 端点:内部服务统一从这里出模型(架构 §5 唯一出口)。

两个入口共用同一套契约与错误语义:

- POST /completions:一次拿全文(短调用省事);
- POST /completions/stream:SSE 逐片增量(对话"边输出边显示"用)。

两个入口都要求内部鉴权(配置了 INTERNAL_SECRET 时)并按 X-Tenant-Id
解析 BYOK 密钥:命中租户自带 key 就用它,否则回退平台 key。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.security import TENANT_HEADER
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from llm_gateway.api.deps import require_internal_secret, resolve_byok_api_key
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
)
from llm_gateway.services import completions as completions_service

router = APIRouter(prefix="/completions", tags=["completions"])

#: SSE 标准头:不缓存、禁代理缓冲(否则增量会被攒成一坨)
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

TenantHeader = Annotated[str | None, Header(alias=TENANT_HEADER)]


def _provider(settings: Settings, model: str | None) -> str:
    """BYOK 查询用的 provider = litellm 模型串的 provider/model 前缀。

    没有前缀时返回空串(不猜提供商):解析器会按 disabled 直接回退平台 key。
    """
    resolved = str(model or settings.default_model)
    return resolved.split("/", 1)[0] if "/" in resolved else ""


async def _resolve_key(
    request: Request, body: CompletionRequest, tenant_id: str | None
) -> str | None:
    settings: Settings = request.app.state.settings
    return await resolve_byok_api_key(
        request,
        provider=_provider(settings, body.model),
        tenant_id=tenant_id,
    )


@router.post(
    "",
    response_model=CompletionResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def create_completion(
    body: CompletionRequest,
    request: Request,
    x_tenant_id: TenantHeader = None,
) -> CompletionResponse:
    settings: Settings = request.app.state.settings
    api_key = await _resolve_key(request, body, x_tenant_id)
    return await completions_service.complete(settings, body, api_key=api_key)


async def _sse(chunks: AsyncIterator[CompletionChunk]) -> AsyncIterator[str]:
    """分片 → SSE 帧:每片一个 data 行,JSON 承载 delta/done/error。"""
    async for chunk in chunks:
        yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


@router.post("/stream", dependencies=[Depends(require_internal_secret)])
async def create_completion_stream(
    body: CompletionRequest,
    request: Request,
    x_tenant_id: TenantHeader = None,
) -> StreamingResponse:
    """流式补全:增量分片;结束片 done=true;失败片 error 非空。

    BYOK 在返回流之前解析完毕——密钥解析失败只会回退平台 key,
    不会在流中途才暴露鉴权问题。
    """
    settings: Settings = request.app.state.settings
    api_key = await _resolve_key(request, body, x_tenant_id)
    return StreamingResponse(
        _sse(completions_service.stream(settings, body, api_key=api_key)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
