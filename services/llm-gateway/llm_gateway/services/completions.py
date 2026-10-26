"""模型调用业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import litellm
from common.errors import UpstreamError
from common.logging import get_logger

from llm_gateway import metrics
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


def _observe_upstream(model: str, code: str) -> None:
    """网关是模型上游的唯一出口:失败按 provider(模型前缀)+ code 计数。"""
    provider = str(model).split("/", 1)[0] or "unknown"
    metrics.observe_llm_error(provider=provider, code=code)


_CONTENT_LIMIT = 2000


def _provider(model: str) -> str:
    """模型 id 的 provider 前缀(litellm 路由格式 provider/model)。"""
    return str(model).split("/", 1)[0] or "unknown"


def _prompt_chars(data: CompletionRequest) -> int:
    return sum(len(str(m.content or "")) for m in data.messages)


def _roles(data: CompletionRequest) -> str:
    """消息角色组合,如 `system+user`(只做低基数标签,不含正文)。"""
    seen: list[str] = []
    for message in data.messages:
        role = str(message.role)
        if role not in seen:
            seen.append(role)
    return "+".join(seen)


def _clip(text: str) -> str:
    text = str(text or "")
    return text if len(text) <= _CONTENT_LIMIT else text[:_CONTENT_LIMIT] + "…(截断)"


def _record_usage(usage: UsageOut | None, model: str) -> None:
    """token 用量按 input/output 两个方向计入 llm_tokens_total。"""
    if usage is None:
        return
    metrics.observe_llm_tokens(
        model=model, direction="input", tokens=usage.prompt_tokens
    )
    metrics.observe_llm_tokens(
        model=model, direction="output", tokens=usage.completion_tokens
    )


def _log_content(settings: Settings, log: Any, direction: str, payload: Any) -> None:
    """`LOG_CONTENT=true` 时的正文快照(默认关:prompt 可能含用户数据)。

    显式开启即视为"要看正文",所以用 info(不必再叠 LOG_LEVEL=DEBUG)。
    """
    if settings.log_content:
        log.info("llm_content", direction=direction, payload=payload)


def _provider_kwargs(settings: Settings, api_key: str | None = None) -> dict[str, Any]:
    """把模型凭据显式传给 litellm(空值不覆盖 litellm 默认)。

    api_key 是请求级密钥(BYOK 解析结果):优先于平台 key,但 api_base 仍用
    平台配置(租户只带密钥,不接管路由终点)。
    """
    extra: dict[str, Any] = {}
    effective_key = api_key or settings.api_key
    if effective_key:
        extra["api_key"] = effective_key
    if settings.api_base:
        extra["api_base"] = settings.api_base
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


class _ToolCallAssembler:
    """流式 tool_calls 装配:litellm 按 index 分片下发,这里合并成完整调用。

    分片规则(OpenAI 兼容):``id``/``name`` 通常只在首片出现,``arguments`` 是字符串
    片段、按到达顺序拼接;对象式与 dict 式分片都要兼容。半截调用不单独下发——
    完整列表只随收尾 done 片整包带出,中途失败时不会有半截 JSON 漏给调用方。
    """

    def __init__(self) -> None:
        self._parts: dict[int, dict[str, str]] = {}

    def add(self, chunk: Any) -> None:
        """吸收一片流式响应里的 tool_calls 分片(该片没有则跳过)。"""
        choices = _get(chunk, "choices") or []
        if not choices:
            return
        delta = _get(choices[0], "delta")
        for raw in _get(delta, "tool_calls") or []:
            index = _get(raw, "index")
            # index 缺省(部分提供商单调用不分片标号)按 0 处理
            slot = self._parts.setdefault(
                index if isinstance(index, int) else 0,
                {"id": "", "name": "", "arguments": ""},
            )
            call_id = _get(raw, "id")
            if call_id:
                slot["id"] = str(call_id)
            function = _get(raw, "function")
            name = _get(function, "name")
            if name:
                slot["name"] = str(name)
            arguments = _get(function, "arguments")
            if arguments:
                slot["arguments"] += str(arguments)

    def calls(self) -> list[ToolCall] | None:
        """装配结果:按 index 排序;没有任何分片时返回 None(调用方据 None 省键)。"""
        if not self._parts:
            return None
        return [
            ToolCall(
                id=slot["id"],
                function=ToolFunction(
                    name=slot["name"], arguments=slot["arguments"] or "{}"
                ),
            )
            for _, slot in sorted(self._parts.items())
        ]


async def complete(
    settings: Settings,
    data: CompletionRequest,
    *,
    api_key: str | None = None,
) -> CompletionResponse:
    model = data.model or settings.default_model
    log = get_logger("llm_gateway.completions")
    extra = _extra(data)
    provider = _provider(model)
    started = time.perf_counter()
    log.info(
        "llm_request",
        provider=provider,
        model=model,
        stream=False,
        message_count=len(data.messages),
        roles=_roles(data),
        prompt_chars=_prompt_chars(data),
        tool_count=len(data.tools or []),
        temperature=data.temperature,
    )
    _log_content(
        settings,
        log,
        "request",
        [{"role": m.role, "content": _clip(m.content or "")} for m in data.messages],
    )
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[m.model_dump(exclude_none=True) for m in data.messages],
            temperature=data.temperature,
            timeout=settings.request_timeout_seconds,
            **_provider_kwargs(settings, api_key),
            **extra,
        )
    except Exception as exc:
        _observe_upstream(model, "llm.upstream_error")
        raise UpstreamError(
            "llm.upstream_error",
            f"模型调用失败:{exc}",
            details={"model": model},
        ) from exc
    duration_seconds = time.perf_counter() - started
    usage_obj = _usage(getattr(resp, "usage", None))
    message = resp.choices[0].message
    content = getattr(message, "content", None) or ""
    tool_calls = _normalize_tool_calls(message)
    resolved_model = str(getattr(resp, "model", None) or model)
    _record_usage(usage_obj, resolved_model)
    metrics.observe_llm_latency(
        provider=provider, model=resolved_model, seconds=duration_seconds
    )
    log.info(
        "llm_response",
        provider=provider,
        model=resolved_model,
        finish_reason=str(_get(resp.choices[0], "finish_reason") or "stop"),
        prompt_tokens=usage_obj.prompt_tokens if usage_obj else 0,
        completion_tokens=usage_obj.completion_tokens if usage_obj else 0,
        total_tokens=usage_obj.total_tokens if usage_obj else 0,
        tool_calls=len(tool_calls),
        chars=len(content),
        duration_ms=round(duration_seconds * 1000, 1),
    )
    _log_content(settings, log, "response", _clip(content))
    return CompletionResponse(
        model=getattr(resp, "model", None) or model,
        content=getattr(message, "content", None) or "",
        tool_calls=tool_calls or None,
        usage=usage_obj,
    )


async def stream(
    settings: Settings,
    data: CompletionRequest,
    *,
    api_key: str | None = None,
) -> AsyncIterator[CompletionChunk]:
    """流式补全:逐片产出增量正文,最后一片 done=true 带全文、用量与 tool_calls。

    失败一律以 error 片收尾——HTTP 头早已发出(200),状态码改不了;
    调用方按 error.code 处理(与 complete 的 502 语义对齐,只是换了载体)。
    上游的 tool_calls 分片按 index 装配,只随 done 片整包带出:中途失败时
    不会吐出半截 JSON,正文增量片也不带 tool_calls。
    """
    model = data.model or settings.default_model
    log = get_logger("llm_gateway.completions")
    extra = _extra(data)
    provider = _provider(model)
    started = time.perf_counter()
    log.info(
        "llm_request",
        provider=provider,
        model=model,
        stream=True,
        message_count=len(data.messages),
        roles=_roles(data),
        prompt_chars=_prompt_chars(data),
        tool_count=len(data.tools or []),
        temperature=data.temperature,
    )
    _log_content(
        settings,
        log,
        "request",
        [{"role": m.role, "content": _clip(m.content or "")} for m in data.messages],
    )
    try:
        resp = await litellm.acompletion(
            model=model,
            messages=[m.model_dump(exclude_none=True) for m in data.messages],
            temperature=data.temperature,
            timeout=settings.request_timeout_seconds,
            stream=True,
            **_provider_kwargs(settings, api_key),
            **extra,
        )
    except Exception as exc:
        _observe_upstream(model, "llm.upstream_error")
        yield CompletionChunk(
            error=StreamError(code="llm.upstream_error", message=f"模型调用失败:{exc}")
        )
        return
    parts: list[str] = []
    tool_calls_in = _ToolCallAssembler()
    usage_out: UsageOut | None = None
    resolved_model = model
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
        _observe_upstream(model, "llm.upstream_error")
        yield CompletionChunk(
            error=StreamError(code="llm.upstream_error", message=f"流式调用中断:{exc}")
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
    )
