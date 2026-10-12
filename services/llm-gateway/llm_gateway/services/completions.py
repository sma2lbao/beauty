"""模型调用业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。"""

from __future__ import annotations

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionRequest,
    CompletionResponse,
    UsageOut,
)


async def complete(settings: Settings, data: CompletionRequest) -> CompletionResponse:
    model = data.model or settings.default_model
    log = get_logger("llm_gateway.completions")
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[m.model_dump() for m in data.messages],
            temperature=data.temperature,
            timeout=settings.request_timeout_seconds,
        )
    except Exception as exc:
        raise UpstreamError(
            "llm.upstream_error",
            f"模型调用失败:{exc}",
            details={"model": model},
        ) from exc
    usage = getattr(resp, "usage", None)
    usage_obj = None
    if usage is not None:
        usage_obj = UsageOut(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )
    log.info("llm_call", model=model, has_usage=usage_obj is not None)
    return CompletionResponse(
        model=getattr(resp, "model", None) or model,
        content=resp.choices[0].message.content or "",
        usage=usage_obj,
    )
