"""模型调用业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    StreamError,
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


def _extra(data: CompletionRequest) -> dict[str, Any]:
    """工具相关参数透传(OpenAI 原生形状,上层不用改)。"""
    extra: dict[str, Any] = {}
    if data.tools:
        extra["tools"] = data.tools
        if data.tool_choice:
            extra["tool_choice"] = data.tool_choice
    return extra


def _usage(raw: Any) -> UsageOut | None:
    if raw is None:
        return None
    return UsageOut(
        prompt_tokens=getattr(raw, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(raw, "completion_tokens", 0) or 0,
        total_tokens=getattr(raw, "total_tokens", 0) or 0,
    )


def _delta_text(chunk: Any) -> str:
    """流片里的增量正文;litellm 对象式与 dict 式都要兼容。"""
    choices = _get(chunk, "choices") or []
    if not choices:
        return ""
    delta = _get(choices[0], "delta")
    return str(_get(delta, "content") or "")


async def complete(settings: Settings, data: CompletionRequest) -> CompletionResponse:
    model = data.model or settings.default_model
    log = get_logger("llm_gateway.completions")
    extra = _extra(data)
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
    usage_obj = _usage(getattr(resp, "usage", None))
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


async def stream(
    settings: Settings, data: CompletionRequest
) -> AsyncIterator[CompletionChunk]:
    """流式补全:逐片产出增量,最后一片 done=true 带全文与用量。

    失败一律以 error 片收尾——HTTP 头早已发出(200),状态码改不了;
    调用方按 error.code 处理(与 complete 的 502 语义对齐,只是换了载体)。
    工具调用的增量拼装暂无调用方,这里只透传正文(需要时再补)。
    """
    model = data.model or settings.default_model
    log = get_logger("llm_gateway.completions")
    extra = _extra(data)
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[m.model_dump(exclude_none=True) for m in data.messages],
            temperature=data.temperature,
            timeout=settings.request_timeout_seconds,
            stream=True,
            **extra,
        )
    except Exception as exc:
        yield CompletionChunk(
            error=StreamError(code="llm.upstream_error", message=f"模型调用失败:{exc}")
        )
        return
    parts: list[str] = []
    usage_out: UsageOut | None = None
    resolved_model = model
    try:
        async for chunk in resp:
            resolved_model = str(_get(chunk, "model") or resolved_model)
            usage_out = _usage(_get(chunk, "usage")) or usage_out
            delta = _delta_text(chunk)
            if delta:
                parts.append(delta)
                yield CompletionChunk(delta=delta)
    except Exception as exc:
        yield CompletionChunk(
            error=StreamError(code="llm.upstream_error", message=f"流式调用中断:{exc}")
        )
        return
    content = "".join(parts)
    log.info("llm_stream", model=resolved_model, chars=len(content))
    yield CompletionChunk(
        done=True, content=content, model=resolved_model, usage=usage_out
    )
