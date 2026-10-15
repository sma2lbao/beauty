"""completions 端点:内部服务统一从这里出模型(架构 §5 唯一出口)。

两个入口共用同一套契约与错误语义:

- POST /completions:一次拿全文(短调用省事);
- POST /completions/stream:SSE 逐片增量(对话"边输出边显示"用)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

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


@router.post("", response_model=CompletionResponse)
async def create_completion(
    body: CompletionRequest, request: Request
) -> CompletionResponse:
    settings: Settings = request.app.state.settings
    return await completions_service.complete(settings, body)


async def _sse(chunks: AsyncIterator[CompletionChunk]) -> AsyncIterator[str]:
    """分片 → SSE 帧:每片一个 data 行,JSON 承载 delta/done/error。"""
    async for chunk in chunks:
        yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


@router.post("/stream")
async def create_completion_stream(
    body: CompletionRequest, request: Request
) -> StreamingResponse:
    """流式补全:增量分片;结束片 done=true;失败片 error 非空。"""
    settings: Settings = request.app.state.settings
    return StreamingResponse(
        _sse(completions_service.stream(settings, body)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
