"""completions 端点:内部服务统一从这里出模型(架构 §5 唯一出口)。

两个入口共用同一套契约与错误语义:

- POST /completions:一次拿全文(短调用省事);
- POST /completions/stream:SSE 逐片增量(对话"边输出边显示"用)。

两个入口都要求内部鉴权(配置了 INTERNAL_SECRET 时)并按 X-Account-Id
解析 BYOK 密钥:命中账号自带 key 就用它,否则回退平台 key。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.security import ACCOUNT_HEADER
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from llm_gateway.api.deps import (
    require_internal_secret,
    resolve_byok_api_key,
    resolve_custom_models,
)
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    validate_request,
)
from llm_gateway.services import completions as completions_service

router = APIRouter(prefix="/completions", tags=["completions"])

#: SSE 标准头:不缓存、禁代理缓冲(否则增量会被攒成一坨)
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

AccountHeader = Annotated[str | None, Header(alias=ACCOUNT_HEADER)]


def _provider(model: str) -> str:
    """BYOK 查询用的 provider = litellm 模型串的 provider/model 前缀。

    没有前缀时返回空串(不猜提供商):解析器会按 disabled 直接回退平台 key。
    """
    return model.split("/", 1)[0] if "/" in model else ""


async def _prepare(
    settings: Settings,
    body: CompletionRequest,
    request: Request,
    account_id: str | None,
) -> tuple[str, list[str]]:
    """入口层体检 + 主模型解析:必须早于开流,错误才能还是 400(§5.1/§3)。

    先取账号 BYOK 自定义模型,再解析模型:账号自己开通的模型不应被平台
    白名单拦下。返回 (主模型, 自定义模型),后者要一路传给服务层——流式
    生成器内部还会再解析一次模型,不传就会被误判 400。
    """
    validate_request(
        body,
        max_images=settings.max_images_per_request,
        max_image_data_chars=settings.max_image_data_chars,
    )
    extra = await resolve_custom_models(request, account_id)
    model = completions_service.resolve_model(settings, body.model, extra_models=extra)
    return model, extra


async def _resolve_key(
    request: Request, account_id: str | None, model: str
) -> str | None:
    return await resolve_byok_api_key(
        request,
        provider=_provider(model),
        account_id=account_id,
    )


@router.post(
    "",
    response_model=CompletionResponse,
    dependencies=[Depends(require_internal_secret)],
)
async def create_completion(
    body: CompletionRequest,
    request: Request,
    x_account_id: AccountHeader = None,
) -> CompletionResponse:
    settings: Settings = request.app.state.settings
    model, extra = await _prepare(settings, body, request, x_account_id)
    api_key = await _resolve_key(request, x_account_id, model)
    return await completions_service.complete(
        settings, body, api_key=api_key, extra_models=extra
    )


async def _sse(chunks: AsyncIterator[CompletionChunk]) -> AsyncIterator[str]:
    """分片 → SSE 帧:每片一个 data 行,JSON 承载 delta/done/error。"""
    async for chunk in chunks:
        yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


@router.post("/stream", dependencies=[Depends(require_internal_secret)])
async def create_completion_stream(
    body: CompletionRequest,
    request: Request,
    x_account_id: AccountHeader = None,
) -> StreamingResponse:
    """流式补全:增量分片;结束片 done=true;失败片 error 非空。

    BYOK 在返回流之前解析完毕——密钥解析失败只会回退平台 key,
    不会在流中途才暴露鉴权问题。
    """
    settings: Settings = request.app.state.settings
    model, extra = await _prepare(settings, body, request, x_account_id)
    api_key = await _resolve_key(request, x_account_id, model)
    return StreamingResponse(
        _sse(
            completions_service.stream(
                settings, body, api_key=api_key, extra_models=extra
            )
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
