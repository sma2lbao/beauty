"""Langfuse 接入(OTel 原生,见 docs/design/langfuse-observability.md)。

设计:

- **不引入 langfuse SDK**:Langfuse 是 OTLP 后端,官方也推荐纯 OTel 写入。
  这样写入端只有一个 TracerProvider(避免与 `common.tracing` 的全局 provider
  争用),后端仍可替换(换 Tempo/Phoenix 只改 endpoint);
- **本模块只做三件事**:导出目标(端点/认证头)、属性映射(把业务语义翻译成
  `langfuse.*` / `gen_ai.*` 属性)、baggage 传播(让 trace 级属性落到每个
  span,便于按 session/user/tags 过滤聚合);
- **零新依赖**:baggage 处理器用官方 `opentelemetry.sdk.trace.SpanProcessor`
  自己实现,不引 `opentelemetry-processor-baggage`。

约定:baggage 会随 HTTP 头跨服务、甚至传给第三方 API,因此只放标识符
(session/user/trace 名/tags/release),**不放 prompt、正文、密钥等敏感内容**。
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace import Span, SpanProcessor

from common.config import BaseAppSettings

#: OTLP/HTTP trace 导出路径(Langfuse ≥ v3.22;v4 实时摄取建议加 ingestion 头)。
OTLP_TRACES_PATH = "/api/public/otel/v1/traces"
#: v4 实时摄取版本头,不带时写入延迟最多 10 分钟(官方文档)。
INGESTION_VERSION_HEADER = "x-langfuse-ingestion-version"
INGESTION_VERSION = "4"

# --- trace 级属性(必须出现在 trace 内每个 span 上,否则无法过滤聚合) ---
TRACE_NAME = "langfuse.trace.name"
TRACE_TAGS = "langfuse.trace.tags"
TRACE_METADATA_PREFIX = "langfuse.trace.metadata."
USER_ID = "langfuse.user.id"
SESSION_ID = "langfuse.session.id"
RELEASE = "langfuse.release"
VERSION = "langfuse.version"

# --- observation 级属性 ---
OBSERVATION_TYPE = "langfuse.observation.type"
OBSERVATION_INPUT = "langfuse.observation.input"
OBSERVATION_OUTPUT = "langfuse.observation.output"
OBSERVATION_METADATA_PREFIX = "langfuse.observation.metadata."
OBSERVATION_MODEL = "langfuse.observation.model.name"
OBSERVATION_USAGE = "langfuse.observation.usage_details"
OBSERVATION_COST = "langfuse.observation.cost_details"
OBSERVATION_LEVEL = "langfuse.observation.level"
OBSERVATION_STATUS_MESSAGE = "langfuse.observation.status_message"
OBSERVATION_COMPLETION_START = "langfuse.observation.completion_start_time"

# --- gen_ai 语义约定(OTel GenAI semconv;Langfuse 与其它后端都识别) ---
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
GEN_AI_USAGE_CACHE_READ = "gen_ai.usage.cache_read_input_tokens"
GEN_AI_USAGE_CACHE_WRITE = "gen_ai.usage.cache_creation_input_tokens"
GEN_AI_USAGE_REASONING = "gen_ai.usage.reasoning_tokens"

#: observation 类型(显式设置优先;不设时 Langfuse 按 model 推断 generation)。
ObservationType = str  # 取值见 OBSERVATION_TYPES

OBSERVATION_TYPES = frozenset(
    {
        "span",
        "generation",
        "event",
        "embedding",
        "agent",
        "tool",
        "chain",
        "retriever",
        "guardrail",
        "evaluator",
    }
)

#: 允许经 baggage 传播的属性(只放标识符,见模块 docstring)。
_BAGGAGE_PREFIXES = ("langfuse.",)


def is_enabled(settings: BaseAppSettings) -> bool:
    """Langfuse 三件套(开关 + public/secret key)齐全才算启用。"""
    return bool(
        settings.langfuse_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def langfuse_endpoint(settings: BaseAppSettings) -> str:
    """Langfuse OTLP trace 端点(host 允许带或不带尾部斜杠)。"""
    return settings.langfuse_host.rstrip("/") + OTLP_TRACES_PATH


def langfuse_headers(settings: BaseAppSettings) -> dict[str, str]:
    """Basic 认证头 + v4 摄取版本头。"""
    public_key = settings.langfuse_public_key or ""
    secret_key = settings.langfuse_secret_key or ""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {
        "Authorization": f"Basic {token}",
        INGESTION_VERSION_HEADER: INGESTION_VERSION,
    }


def export_target(
    settings: BaseAppSettings,
) -> tuple[str, dict[str, str]] | None:
    """启用了 Langfuse 时返回 (endpoint, headers),否则 None。"""
    if not is_enabled(settings):
        return None
    return langfuse_endpoint(settings), langfuse_headers(settings)


def json_value(value: Any) -> str:
    """Langfuse 的 input/output/usage/cost 字段要求 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _put(attributes: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        attributes[key] = value


def trace_attributes(
    *,
    trace_name: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    tags: list[str] | None = None,
    release: str | None = None,
    version: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 trace 级属性:既写给当前 span,也可放进 baggage 供子 span 继承。"""
    attributes: dict[str, Any] = {}
    _put(attributes, TRACE_NAME, trace_name)
    _put(attributes, SESSION_ID, session_id)
    _put(attributes, USER_ID, user_id)
    if tags:
        attributes[TRACE_TAGS] = list(tags)
    _put(attributes, RELEASE, release)
    _put(attributes, VERSION, version)
    for key, value in (metadata or {}).items():
        attributes[f"{TRACE_METADATA_PREFIX}{key}"] = json_value(value)
    return attributes


def observation_attributes(
    *,
    observation_type: ObservationType | None = None,
    input: Any = None,
    output: Any = None,
    model: str | None = None,
    usage: Mapping[str, int | float] | None = None,
    cost: Mapping[str, float] | float | None = None,
    level: str | None = None,
    status_message: str | None = None,
    completion_start_time: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 observation 级属性(generation/tool/agent/… 的网关与工具 span)。"""
    attributes: dict[str, Any] = {}
    if observation_type is not None:
        if observation_type not in OBSERVATION_TYPES:
            raise ValueError(f"unknown observation type: {observation_type}")
        attributes[OBSERVATION_TYPE] = observation_type
    if input is not None:
        attributes[OBSERVATION_INPUT] = json_value(input)
    if output is not None:
        attributes[OBSERVATION_OUTPUT] = json_value(output)
    _put(attributes, OBSERVATION_MODEL, model)
    if usage is not None:
        attributes[OBSERVATION_USAGE] = json_value(usage)
    if cost is not None:
        attributes[OBSERVATION_COST] = json_value(
            {"total": float(cost)} if isinstance(cost, (int, float)) else cost
        )
    _put(attributes, OBSERVATION_LEVEL, level)
    _put(attributes, OBSERVATION_STATUS_MESSAGE, status_message)
    _put(attributes, OBSERVATION_COMPLETION_START, completion_start_time)
    for key, value in (metadata or {}).items():
        attributes[f"{OBSERVATION_METADATA_PREFIX}{key}"] = json_value(value)
    return attributes


def gen_ai_usage_attributes(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> dict[str, int]:
    """gen_ai.usage.* 标准属性;Langfuse 直接据此汇总 token 与成本。"""
    attributes: dict[str, int] = {}
    _put(attributes, GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    _put(attributes, GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
    _put(attributes, GEN_AI_USAGE_TOTAL_TOKENS, total_tokens)
    _put(attributes, GEN_AI_USAGE_CACHE_READ, cache_read_tokens)
    _put(attributes, GEN_AI_USAGE_CACHE_WRITE, cache_write_tokens)
    _put(attributes, GEN_AI_USAGE_REASONING, reasoning_tokens)
    return attributes


def apply_attributes(span: Span, attributes: Mapping[str, Any]) -> None:
    """把属性写到 span(非 recording span 上静默跳过)。"""
    if not span.is_recording():
        return
    for key, value in attributes.items():
        span.set_attribute(key, value)


def set_trace_attributes(span: Span, **kwargs: Any) -> None:
    """把 trace 级属性写到指定 span(不传播;传播用 `langfuse_scope`)。"""
    apply_attributes(span, trace_attributes(**kwargs))


@contextmanager
def langfuse_scope(**kwargs: Any) -> Iterator[None]:
    """在 trace 根位置使用:写当前 span 的属性 + 放进 baggage 供子 span 继承。

    退出时 detach,避免同进程后续任务(worker 循环)串味。子任务/子协程创建时
    会复制 context,因此作用域内派生的执行链都能读到这些属性。
    """
    attributes = trace_attributes(**kwargs)
    apply_attributes(trace.get_current_span(), attributes)
    token = None
    ctx = context.get_current()
    for key, value in attributes.items():
        raw = value if isinstance(value, str) else json_value(value)
        ctx = baggage.set_baggage(key, raw, context=ctx)
    if ctx is not None:
        token = context.attach(ctx)
    try:
        yield
    finally:
        if token is not None:
            context.detach(token)


class LangfuseBaggageSpanProcessor(SpanProcessor):
    """把 `langfuse.*` baggage 复制到每个新 span(跨进程与嵌套调用一致)。

    Langfuse 要求 trace 级属性出现在 trace 内每个 span 上,否则按 session/user/
    tags 的过滤聚合会漏;官方推荐 BaggageSpanProcessor。tags 在 baggage 里是
    JSON 字符串,这里解析回 list 以满足 Langfuse 的 string[] 语义。
    """

    def on_start(self, span: Span, parent_context: Any = None) -> None:  # noqa: ANN401 - OTel 签名
        for key, value in baggage.get_all(context=parent_context).items():
            if not key.startswith(_BAGGAGE_PREFIXES):
                continue
            if key == TRACE_TAGS and isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, list):
                    span.set_attribute(key, [str(item) for item in parsed])
                continue
            if isinstance(value, str):
                span.set_attribute(key, value)

    def on_end(self, span: Span) -> None:
        return

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
