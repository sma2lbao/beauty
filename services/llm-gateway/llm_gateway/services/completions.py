"""模型调用业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。"""

from __future__ import annotations

from typing import Any

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionRequest,
    CompletionResponse,
    ToolCall,
    ToolFunction,
    UsageOut,
)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """兼容 litellm 的对象式响应与部分提供商的 dict 式响应。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_tool_calls(message: Any) -> list[ToolCall]:
    """litellm 的 tool_calls 对象/字典 → 归一化 ToolCall;缺字段保守跳过。"""
    calls: list[ToolCall] = []
    for raw in _get(message, "tool_calls") or []:
        function = _get(raw, "function")
        if function is None:
            continue
        calls.append(
            ToolCall(
                id=str(_get(raw, "id") or ""),
                function=ToolFunction(
                    name=str(_get(function, "name") or ""),
                    arguments=str(_get(function, "arguments") or "{}"),
                ),
            )
        )
    return calls


async def complete(settings: Settings, data: CompletionRequest) -> CompletionResponse:
    model = data.model or settings.default_model
    log = get_logger("llm_gateway.completions")
    extra: dict[str, Any] = {}
    if data.tools:
        extra["tools"] = data.tools
        if data.tool_choice:
            extra["tool_choice"] = data.tool_choice
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[m.model_dump(exclude_none=True) for m in data.messages],
            temperature=data.temperature,
            timeout=settings.request_timeout_seconds,
            **extra,
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
    message = resp.choices[0].message
    tool_calls = _normalize_tool_calls(message)
    log.info(
        "llm_call",
        model=model,
        has_usage=usage_obj is not None,
        tool_calls=len(tool_calls),
    )
    return CompletionResponse(
        model=getattr(resp, "model", None) or model,
        content=getattr(message, "content", None) or "",
        tool_calls=tool_calls or None,
        usage=usage_obj,
    )
