"""completions 端点:内部服务统一从这里出模型(架构 §5 唯一出口)。"""

from __future__ import annotations

from fastapi import APIRouter, Request

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import CompletionRequest, CompletionResponse
from llm_gateway.services import completions as completions_service

router = APIRouter(prefix="/completions", tags=["completions"])


@router.post("", response_model=CompletionResponse)
async def create_completion(
    body: CompletionRequest, request: Request
) -> CompletionResponse:
    settings: Settings = request.app.state.settings
    return await completions_service.complete(settings, body)
