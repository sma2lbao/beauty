"""模型调用业务逻辑:litellm 是唯一出口(架构 §5);失败统一映射 502。

模型选择与 fallback(``docs/design/model-routing.md`` §3/§5.3):

- 主模型 = 请求显式指定,否则网关默认;``AVAILABLE_MODELS`` 非空时做白名单校验;
- 候选链 = 主模型 + ``FALLBACK_MODELS``(去重、保序);
- 只有"临时性"故障才切下一个候选(限流/超时/连接失败/5xx),参数或鉴权类
  4xx 直接 502——重试另一个模型只会掩盖调用方的错误。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import litellm
from common.errors import AppError, UpstreamError
from common.logging import get_logger

from llm_gateway import metrics
from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ContentPart,
    FallbackHop,
    ImageUrlContentPart,
    StreamError,
    TextContentPart,
    ToolCall,
    ToolFunction,
    UsageOut,
    image_urls,
    text_chars,
)

#: 可切换候选的 HTTP 状态码:限流/超时/上游 5xx(网关语义,不是调用方问题)
_FALLBACK_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

#: 可切换的 litellm 异常类型。``APIStatusError`` 在 litellm 顶层不稳定导出,
#: 所以状态码走鸭子判定(见 ``_is_switchable``)。
_SWITCHABLE_ERRORS = (
    litellm.RateLimitError,
    litellm.Timeout,
    litellm.APIConnectionError,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
)

#: fallback 原因码:与 502 的错误码保持一致(契约示例)
_FALLBACK_CODE = "llm.upstream_error"


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
    """工具与结构化输出参数透传(OpenAI 原生形状,上层不用改)。"""
    extra: dict[str, Any] = {}
    if data.tools:
        extra["tools"] = data.tools
        if data.tool_choice:
            extra["tool_choice"] = data.tool_choice
    if data.response_format is not None:
        extra["response_format"] = data.response_format
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
    """提示词文本总长(多模态只算文字片段,图片不计字节数)。"""
    return sum(text_chars(message) for message in data.messages)


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


def _content_preview(parts: list[ContentPart] | str | None) -> str:
    """日志里的内容预览:图片只留占位符,绝不落 data URL 正文。"""
    if isinstance(parts, str):
        return _clip(parts)
    if not parts:
        return ""
    previews: list[str] = []
    for part in parts:
        if isinstance(part, TextContentPart):
            previews.append(_clip(part.text))
        elif isinstance(part, ImageUrlContentPart):
            previews.append("[image]")
    return " | ".join(previews)


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


def resolve_model(settings: Settings, requested: str | None) -> str:
    """解析最终主模型并做白名单校验(model-routing §3)。

    显式模型 > 网关默认;``AVAILABLE_MODELS`` 非空时白名单外的模型返回 400,
    留空则不限制(只按默认模型语义校验)。
    """
    model = requested or settings.default_model
    if settings.available_models and model not in settings.available_models:
        raise AppError(
            "llm.model_not_allowed",
            f"模型不在允许列表内:{model}",
            details={"model": model, "available_models": settings.available_models},
        )
    return model


def candidates(settings: Settings, model: str) -> list[str]:
    """候选链 = 主模型 + 备用模型(去重、保序,主模型永远排第一)。"""
    chain = [model]
    for fallback in settings.fallback_models:
        if fallback and fallback not in chain:
            chain.append(fallback)
    return chain


def _is_switchable(exc: BaseException) -> bool:
    """临时性故障才可切换:限流/超时/连接失败/5xx(状态码鸭子判定)。"""
    if isinstance(exc, _SWITCHABLE_ERRORS):
        return True
    return getattr(exc, "status_code", None) in _FALLBACK_STATUS_CODES


def _record_fallback(from_model: str, to_model: str, exc: BaseException) -> FallbackHop:
    """记录一次 fallback:warning 日志 + 指标 + 响应里的 hop(§5.2/§5.3)。"""
    message = _clip_short(str(exc))
    log = get_logger("llm_gateway.completions")
    log.warning(
        "llm_fallback",
        from_model=from_model,
        to_model=to_model,
        code=_FALLBACK_CODE,
        error=message,
    )
    metrics.observe_llm_fallback(
        from_model=from_model,
        to_model=to_model,
        code=_FALLBACK_CODE,
    )
    return FallbackHop(
        from_model=from_model,
        to_model=to_model,
        code=_FALLBACK_CODE,
        message=message,
    )


def _clip_short(text: str) -> str:
    """fallback message 截断到 500 字以内(§5.2),避免上游长报错灌进响应。"""
    text = str(text or "")
    return text if len(text) <= 500 else text[:499] + "…"


def _failure_details(
    model: str,
    attempts: list[dict[str, str]],
    fallbacks: list[FallbackHop],
) -> dict[str, Any]:
    """502 的 details:主模型、已试候选与错误、以及切换记录。"""
    return {
        "model": model,
        "attempts": attempts,
        "fallbacks": [hop.model_dump() for hop in fallbacks],
    }


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


def _log_request(
    settings: Settings,
    log: Any,
    data: CompletionRequest,
    model: str,
    *,
    stream: bool,
) -> None:
    log.info(
        "llm_request",
        provider=_provider(model),
        model=model,
        stream=stream,
        message_count=len(data.messages),
        roles=_roles(data),
        prompt_chars=_prompt_chars(data),
        tool_count=len(data.tools or []),
        image_count=len(image_urls(data.messages)),
        temperature=data.temperature,
    )
    _log_content(
        settings,
        log,
        "request",
        [
            {"role": message.role, "content": _content_preview(message.content)}
            for message in data.messages
        ],
    )


async def _request(
    settings: Settings,
    data: CompletionRequest,
    model: str,
    api_key: str | None,
    *,
    stream: bool,
) -> Any:
    return await litellm.acompletion(
        model=model,
        messages=[m.model_dump(exclude_none=True) for m in data.messages],
        temperature=data.temperature,
        timeout=settings.request_timeout_seconds,
        **_provider_kwargs(settings, api_key),
        **_extra(data),
        **({"stream": True} if stream else {}),
    )


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
