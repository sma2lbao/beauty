"""导出前属性脱敏(SpanProcessor,见 docs/design/langfuse-observability.md §9)。

为什么需要:OTel 的 span 属性是"导出即离开进程",而 Langfuse SDK 的
`mask_otel_spans` 只在官方 SDK 中存在,纯 OTel 写入没有等价物。业务代码里的
`LOG_CONTENT` 开关只控制"要不要主动写正文",防不住第三方 instrumentation
(httpx/FastAPI/未来的 GenAI 库)顺手带上请求头、URL query、prompt 正文。

本处理器挂在 **TracerProvider 的第一个位置**(早于 Batch/SimpleSpanProcessor),
在 span 结束、进入导出队列前统一清洗:

1. **敏感键一律删除**:Authorization/Cookie/api_key/secret/password/credential/
   单数 token、以及所有 `http.request.header.*`(默认不采集,采集了也不能导出);
2. **正文闸门**:LLM prompt/补全/消息(`langfuse.observation.input|output`、
   `gen_ai.input.*`/`gen_ai.output.*`/`gen_ai.system_instructions`、
   `gen_ai.tool.call.arguments|result` 等)只有 `LOG_CONTENT=true` 才保留;
   HTTP body 与 `db.statement` 永远删除;
3. **URL 去参**:`url.full`/`http.url`/`http.target` 去掉 query 与 userinfo
   (stream token 会出现在 query 里);
4. **值截断**:字符串 > 2000 字符截断,序列最多 64 项(防止一个 span 撑爆)。

处理器只改属性,不改 span 名/时间/状态;resource 属性不含业务数据,不动。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from opentelemetry.sdk.trace import Span, SpanProcessor

from common import langfuse
from common.logging import get_logger

log = get_logger("redaction")

#: 单个属性值的字符上限:超过就截断(Langfuse/OTLP 都不建议塞大段正文)。
MAX_ATTRIBUTE_CHARS = 2000
#: 序列属性的元素上限。
MAX_SEQUENCE_ITEMS = 64
#: 截断标记放在值尾部,便于在 Langfuse 上识别"不是完整正文"。
TRUNCATION_MARKER = "…[truncated]"

#: 键名包含这些子串即删除(大小写不敏感)。不要加泛化的 "token":
#: `gen_ai.usage.input_tokens` 是 token 计数,必须保留。
_DENIED_SUBSTRINGS = (
    "authorization",
    "cookie",
    "api-key",
    "apikey",
    "api_key",
    "secret",
    "password",
    "passwd",
    "credential",
    "private-key",
    "private_key",
)
#: 按段匹配:单数 token(`session.token` / `x-csrf-token`)是凭证;
#: 复数 tokens 是计数,不在此列。
_DENIED_SEGMENTS = frozenset({"token"})
#: 请求/响应头一律不导出(即使显式打开了 header 采集)。
_DENIED_PREFIXES = ("http.request.header.", "http.response.header.")
#: 永远删除的正文类属性(与 LLM 内容开关无关)。
_ALWAYS_DROP = frozenset(
    {
        "http.request.body",
        "http.response.body",
        "db.statement",
        "db.query.text",
        "url.query",
    }
)
#: 只有 `LOG_CONTENT=true` 才导出的 LLM 正文字段。
_CONTENT_EXACT = frozenset(
    {
        langfuse.OBSERVATION_INPUT,
        langfuse.OBSERVATION_OUTPUT,
        "gen_ai.system_instructions",
        "gen_ai.prompt",
        "gen_ai.completion",
        "input.value",
        "output.value",
        "ai.prompt",
        "ai.completion",
        "llm.prompts",
        "llm.completions",
    }
)
_CONTENT_PREFIXES = (
    "gen_ai.input.",
    "gen_ai.output.",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
)
#: 需要去掉 query/userinfo 的 URL 属性。
_URL_KEYS = frozenset({"url.full", "http.url", "http.target"})

_SEGMENT_SPLIT = re.compile(r"[.\-_]")


def _segments(key: str) -> set[str]:
    return {part for part in _SEGMENT_SPLIT.split(key.lower()) if part}


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered.startswith(_DENIED_PREFIXES):
        return True
    if any(needle in lowered for needle in _DENIED_SUBSTRINGS):
        return True
    return bool(_segments(lowered) & _DENIED_SEGMENTS)


def _is_content(key: str) -> bool:
    return key in _CONTENT_EXACT or key.startswith(_CONTENT_PREFIXES)


def _redact_url(value: str) -> str:
    """去掉 URL 的 query/fragment 与 userinfo(保留 scheme/host/path)。"""
    parts = urlsplit(value)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "[redacted]@" + netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _truncate(value: str) -> str:
    if len(value) <= MAX_ATTRIBUTE_CHARS:
        return value
    return value[:MAX_ATTRIBUTE_CHARS] + TRUNCATION_MARKER


def _sanitize_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        if key in _URL_KEYS:
            return _truncate(_redact_url(value))
        return _truncate(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value)[:MAX_SEQUENCE_ITEMS]
        return [_truncate(item) if isinstance(item, str) else item for item in items]
    return value


def sanitize_attributes(
    attributes: Mapping[str, Any], *, allow_content: bool
) -> dict[str, Any]:
    """返回清洗后的属性副本;纯函数,便于单测与服务外复用。"""
    sanitized: dict[str, Any] = {}
    for key, value in attributes.items():
        if _is_sensitive(key) or key in _ALWAYS_DROP:
            continue
        if not allow_content and _is_content(key):
            continue
        sanitized[key] = _sanitize_value(key, value)
    return sanitized


class AttributeRedactionSpanProcessor(SpanProcessor):
    """在 span 结束、导出之前清洗属性(必须注册在导出处理器之前)。"""

    def __init__(self, *, allow_content: bool = False) -> None:
        self._allow_content = allow_content

    def on_start(self, span: Span, parent_context: Any = None) -> None:  # noqa: ANN401
        return

    def on_end(self, span: Span) -> None:
        # Span.end 传进来的是 ReadableSpan,属性容器是 BoundedAttributes 且此时
        # 已 immutable,不能原地删改;直接换成一个普通 dict(导出器只读属性)。
        attributes = getattr(span, "_attributes", None)
        if attributes is None:
            return
        current = dict(attributes)
        sanitized = sanitize_attributes(current, allow_content=self._allow_content)
        if sanitized == current:
            return
        span._attributes = sanitized  # type: ignore[attr-defined]
        log.debug(
            "span_attributes_redacted",
            removed=len(current) - len(sanitized),
            allow_content=self._allow_content,
        )

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
