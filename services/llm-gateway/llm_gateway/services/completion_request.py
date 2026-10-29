"""litellm 请求组装与请求日志:载荷、凭据、工具/结构化输出透传。

兼容 litellm 的对象式响应与部分提供商的 dict 式响应(``_get``);
正文只在 ``LOG_CONTENT=true`` 时落日志,图片一律只留占位符。
"""

from __future__ import annotations

from typing import Any

import litellm

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import (
    CompletionRequest,
    ContentPart,
    ImageUrlContentPart,
    TextContentPart,
    image_urls,
    text_chars,
)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """兼容 litellm 的对象式响应与部分提供商的 dict 式响应。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
