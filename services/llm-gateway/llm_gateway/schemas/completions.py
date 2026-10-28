"""模型调用契约:OpenAI 风格消息进,归一化响应出(内部服务不感知提供商)。

tools/tool_calls 保持 OpenAI 原生嵌套形状(``function.name/arguments``)——
上层服务回传历史消息时无需再做形状转换,litellm 直接可用。

多模态(``docs/design/model-routing.md`` §5.1):``messages[].content`` 可以是
字符串,也可以是 ``ContentPart`` 列表(文本片段 + 图片片段);图片以
``image_url`` data URL 形式进模型。语义校验(长度/张数/URL 协议)由
``validate_request`` 负责——URL 与数量上限依赖配置,不宜放进 pydantic 模型。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from common.errors import AppError
from pydantic import BaseModel, Field

#: 单次请求所有消息的文本总长上限(model-routing §5.1)
TEXT_CHARS_LIMIT = 100000

#: 图片 URL 只接受平台自己产出的 data URL(也兼容 http(s) 外链,便于联调)
_DATA_IMAGE_PREFIX = re.compile(r"^data:image/(png|jpeg|webp);base64,", re.IGNORECASE)


class ToolFunction(BaseModel):
    name: str
    arguments: str = "{}"  # JSON 字符串原文,由调用方解析


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ToolFunction


class TextContentPart(BaseModel):
    """多模态消息里的文字片段。"""

    type: Literal["text"]
    text: str


class ImageUrl(BaseModel):
    url: str


class ImageUrlContentPart(BaseModel):
    """多模态消息里的图片片段(平台只发 data URL,兼容 http(s) 外链)。"""

    type: Literal["image_url"]
    image_url: ImageUrl


#: 消息内容片段联合(仅这两种,形状与 OpenAI 多模态一致,原样透传 litellm)
ContentPart = TextContentPart | ImageUrlContentPart


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str | list[ContentPart] | None = None
    tool_call_id: str | None = None  # role=tool:回指哪次调用
    tool_calls: list[ToolCall] | None = None  # role=assistant:模型请求的工具


class CompletionRequest(BaseModel):
    model: str | None = None  # 不传走 default_model(§5 路由)
    messages: list[Message] = Field(min_length=1)
    # 结构化输出(§5.1):{"type":"json_object"} 或 {"type":"json_schema",...},
    # 语义由 validate_request 校验后原样透传 litellm
    response_format: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    tools: list[dict[str, Any]] | None = None  # OpenAI tools 格式透传
    tool_choice: str | None = None


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class FallbackHop(BaseModel):
    """一次 fallback 切换:从哪个模型换到哪个、为什么(§5.2)。"""

    from_model: str
    to_model: str
    code: str
    message: str


class CompletionResponse(BaseModel):
    model: str
    content: str
    tool_calls: list[ToolCall] | None = None
    usage: UsageOut | None = None
    # 没切过模型时为 null(§5.2)
    fallbacks: list[FallbackHop] | None = None


class StreamError(BaseModel):
    """流内错误:HTTP 头已发出,错误只能作为一帧送达(调用方按 code 处理)。"""

    code: str
    message: str


class CompletionChunk(BaseModel):
    """流式分片:增量文本进 delta;最后一片 done=true 带全文(便于调用方对账)。

    错误以单独一片(error 非空)送达——流一旦开始,状态码就改不了了。
    tool_calls / fallbacks 只在收尾片整体带出(中途不吐半截数据);没有时
    为 None,SSE 序列化按 exclude_none 省略该键。
    """

    delta: str = ""
    done: bool = False
    content: str | None = None
    model: str | None = None
    usage: UsageOut | None = None
    tool_calls: list[ToolCall] | None = None
    fallbacks: list[FallbackHop] | None = None
    error: StreamError | None = None


def text_chars(message: Message) -> int:
    """消息的文本字数(多模态只算 text 片段,图片计 0 字)。"""
    content = message.content
    if isinstance(content, str):
        return len(content)
    if not content:
        return 0
    return sum(len(part.text) for part in content if isinstance(part, TextContentPart))


def image_urls(messages: Iterable[Message]) -> list[str]:
    """按消息顺序取出所有图片 URL。"""
    urls: list[str] = []
    for message in messages:
        content = message.content
        if not isinstance(content, list):
            continue
        urls.extend(
            part.image_url.url
            for part in content
            if isinstance(part, ImageUrlContentPart)
        )
    return urls


def _validate_messages(
    messages: Iterable[Message], *, max_images: int, max_image_data_chars: int
) -> None:
    total_chars = sum(text_chars(message) for message in messages)
    if total_chars > TEXT_CHARS_LIMIT:
        raise AppError(
            "llm.invalid_message",
            f"文本总长超过上限:{total_chars} > {TEXT_CHARS_LIMIT}",
            details={"chars": total_chars, "max_chars": TEXT_CHARS_LIMIT},
        )
    urls = image_urls(messages)
    if len(urls) > max_images:
        raise AppError(
            "llm.invalid_message",
            f"图片数量超过上限:{len(urls)} > {max_images}",
            details={"images": len(urls), "max_images": max_images},
        )
    for index, url in enumerate(urls):
        if url.startswith(("http://", "https://")):
            continue
        if not _DATA_IMAGE_PREFIX.match(url):
            raise AppError(
                "llm.invalid_message",
                "图片 URL 只支持 data:image/png|jpeg|webp;base64 或 http(s)",
                details={"index": index},
            )
        if len(url) > max_image_data_chars:
            raise AppError(
                "llm.invalid_message",
                f"图片 data URL 超过长度上限:{len(url)} > {max_image_data_chars}",
                details={
                    "index": index,
                    "chars": len(url),
                    "max_chars": max_image_data_chars,
                },
            )


def _validate_response_format(response_format: dict[str, Any] | None) -> None:
    if response_format is None:
        return
    kind = response_format.get("type")
    if kind == "json_object":
        return
    if kind == "json_schema" and isinstance(response_format.get("json_schema"), dict):
        return
    raise AppError(
        "llm.invalid_response_format",
        "response_format 仅支持 json_object 或 json_schema",
        details={"type": kind if isinstance(kind, str) else None},
    )


def validate_request(
    data: CompletionRequest,
    *,
    max_images: int,
    max_image_data_chars: int,
) -> None:
    """请求体语义校验(§5.1):违反即 400,code 见契约。

    放在入口层(而不是服务层)是因为流式入口必须在返回 200 之前拒绝——
    一旦开流,错误只能换 error 帧,状态码改不了。
    """
    _validate_messages(
        data.messages,
        max_images=max_images,
        max_image_data_chars=max_image_data_chars,
    )
    _validate_response_format(data.response_format)
