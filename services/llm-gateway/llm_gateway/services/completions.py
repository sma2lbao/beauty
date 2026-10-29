"""模型调用业务逻辑门面:litellm 是唯一出口(架构 §5);失败统一映射 502。

实现按职责拆在同目录兄弟模块,本模块只保留 ``complete`` / ``stream`` 两个入口,
并重导出内部 helper——既有 import 路径(含私有名,如 ``embeddings._get``)保持不变:

- ``completion_routing``:模型解析/白名单、候选链与 fallback 判定;
- ``completion_request``:litellm 请求组装与请求日志;
- ``completion_response``:响应/流片归一化(用量、增量正文、tool_calls 装配)。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway import metrics
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    FallbackHop,
    StreamError,
    UsageOut,
)
from llm_gateway.services.completion_request import (
    _CONTENT_LIMIT,
    _clip,
    _content_preview,
    _extra,
    _get,
    _log_content,
    _log_request,
    _prompt_chars,
    _provider,
    _provider_kwargs,
    _request,
    _roles,
)
from llm_gateway.services.completion_response import (
    _delta_text,
    _normalize_tool_calls,
    _record_usage,
    _ToolCallAssembler,
    _usage,
)
from llm_gateway.services.completion_routing import (
    _FALLBACK_CODE,
    _FALLBACK_STATUS_CODES,
    _SWITCHABLE_ERRORS,
    _clip_short,
    _failure_details,
    _is_switchable,
    _observe_upstream,
    _record_fallback,
    candidates,
    resolve_model,
)

__all__ = [
    "_CONTENT_LIMIT",
    "_FALLBACK_CODE",
    "_FALLBACK_STATUS_CODES",
    "_SWITCHABLE_ERRORS",
    "_ToolCallAssembler",
    "_clip",
    "_clip_short",
    "_content_preview",
    "_delta_text",
    "_extra",
    "_failure_details",
    "_get",
    "_is_switchable",
    "_log_content",
    "_log_request",
    "_normalize_tool_calls",
    "_observe_upstream",
    "_prompt_chars",
    "_provider",
    "_provider_kwargs",
    "_record_fallback",
    "_record_usage",
    "_request",
    "_roles",
    "_usage",
    "candidates",
    "complete",
    "resolve_model",
    "stream",
]


async def complete(
    settings: Settings,
    data: CompletionRequest,
    *,
    api_key: str | None = None,
) -> CompletionResponse:
    model = resolve_model(settings, data.model)
    log = get_logger("llm_gateway.completions")
    chain = candidates(settings, model)
    _log_request(settings, log, data, model, stream=False)
    attempts: list[dict[str, str]] = []
    fallbacks: list[FallbackHop] = []
    for index, candidate in enumerate(chain):
        next_model = chain[index + 1] if index + 1 < len(chain) else None
        started = time.perf_counter()
        try:
            resp = await _request(settings, data, candidate, api_key, stream=False)
        except Exception as exc:
            _observe_upstream(candidate, _FALLBACK_CODE)
            attempts.append({"model": candidate, "error": _clip_short(str(exc))})
            # 参数/鉴权类错误不可切换;没有下一个候选也直接失败
            if next_model is None or not _is_switchable(exc):
                raise UpstreamError(
                    _FALLBACK_CODE,
                    f"模型调用失败:{exc}",
                    details=_failure_details(model, attempts, fallbacks),
                ) from exc
            fallbacks.append(_record_fallback(candidate, next_model, exc))
            continue
        duration_seconds = time.perf_counter() - started
        provider = _provider(candidate)
        usage_obj = _usage(getattr(resp, "usage", None))
        message = resp.choices[0].message
        content = getattr(message, "content", None) or ""
        tool_calls = _normalize_tool_calls(message)
        resolved_model = str(getattr(resp, "model", None) or candidate)
        _record_usage(usage_obj, resolved_model)
        metrics.observe_llm_latency(
            provider=provider, model=resolved_model, seconds=duration_seconds
        )
        log.info(
            "llm_response",
            provider=provider,
            model=resolved_model,
            stream=False,
            finish_reason=str(_get(resp.choices[0], "finish_reason") or "stop"),
            prompt_tokens=usage_obj.prompt_tokens if usage_obj else 0,
            completion_tokens=usage_obj.completion_tokens if usage_obj else 0,
            total_tokens=usage_obj.total_tokens if usage_obj else 0,
            tool_calls=len(tool_calls),
            fallback_count=len(fallbacks),
            chars=len(content),
            duration_ms=round(duration_seconds * 1000, 1),
        )
        _log_content(settings, log, "response", _clip(content))
        return CompletionResponse(
            model=resolved_model,
            content=content,
            tool_calls=tool_calls or None,
            usage=usage_obj,
            fallbacks=fallbacks or None,
        )
    # 候选链至少含主模型,循环内必然 return/raise;此处仅为类型收窄
    raise UpstreamError(  # pragma: no cover - 防御性分支
        _FALLBACK_CODE,
        "模型调用失败:无可用候选",
        details=_failure_details(model, attempts, fallbacks),
    )


async def stream(
    settings: Settings,
    data: CompletionRequest,
    *,
    api_key: str | None = None,
) -> AsyncIterator[CompletionChunk]:
    """流式补全:逐片产出增量正文,最后一片 done=true 带全文、用量与 tool_calls。

    fallback 只发生在"开流前":某个候选在拿到第一个响应之前临时故障,就换下一个
    候选;一旦正文增量已经下发,失败只能以 error 片收尾(HTTP 头早已发出,
    状态码改不了,重试会让调用方收到重复正文)。错误片不带 fallbacks——
    成功收尾的 done 片才带切换记录(§5.2)。
    """
    model = resolve_model(settings, data.model)
    log = get_logger("llm_gateway.completions")
    chain = candidates(settings, model)
    _log_request(settings, log, data, model, stream=True)
    started = time.perf_counter()
    fallbacks: list[FallbackHop] = []
    resp: Any = None
    chosen = model
    for index, candidate in enumerate(chain):
        next_model = chain[index + 1] if index + 1 < len(chain) else None
        try:
            resp = await _request(settings, data, candidate, api_key, stream=True)
        except Exception as exc:
            _observe_upstream(candidate, _FALLBACK_CODE)
            if next_model is None or not _is_switchable(exc):
                yield CompletionChunk(
                    error=StreamError(
                        code=_FALLBACK_CODE, message=f"模型调用失败:{exc}"
                    )
                )
                return
            fallbacks.append(_record_fallback(candidate, next_model, exc))
            continue
        chosen = candidate
        break
    parts: list[str] = []
    tool_calls_in = _ToolCallAssembler()
    usage_out: UsageOut | None = None
    resolved_model = str(getattr(resp, "model", None) or chosen)
    provider = _provider(chosen)
    try:
        async for chunk in resp:
            resolved_model = str(_get(chunk, "model") or resolved_model)
            usage_out = _usage(_get(chunk, "usage")) or usage_out
            tool_calls_in.add(chunk)
            delta = _delta_text(chunk)
            if delta:
                parts.append(delta)
                yield CompletionChunk(delta=delta)
    except Exception as exc:
        _observe_upstream(resolved_model, _FALLBACK_CODE)
        yield CompletionChunk(
            error=StreamError(code=_FALLBACK_CODE, message=f"流式调用中断:{exc}")
        )
        return
    content = "".join(parts)
    tool_calls = tool_calls_in.calls()
    duration_seconds = time.perf_counter() - started
    _record_usage(usage_out, resolved_model)
    metrics.observe_llm_latency(
        provider=provider, model=resolved_model, seconds=duration_seconds
    )
    log.info(
        "llm_response",
        provider=provider,
        model=resolved_model,
        stream=True,
        finish_reason="stop",
        prompt_tokens=usage_out.prompt_tokens if usage_out else 0,
        completion_tokens=usage_out.completion_tokens if usage_out else 0,
        total_tokens=usage_out.total_tokens if usage_out else 0,
        tool_calls=len(tool_calls or []),
        fallback_count=len(fallbacks),
        chars=len(content),
        duration_ms=round(duration_seconds * 1000, 1),
    )
    _log_content(settings, log, "response", _clip(content))
    yield CompletionChunk(
        done=True,
        content=content,
        model=resolved_model,
        usage=usage_out,
        tool_calls=tool_calls,
        fallbacks=fallbacks or None,
    )
