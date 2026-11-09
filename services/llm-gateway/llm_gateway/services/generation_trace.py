"""把一次模型调用映射成 Langfuse generation observation(纯 OTel,无 SDK)。

放在 llm-gateway:全平台唯一模型出口,所有调用(含 orchestrator/知识库/子代理)
都会经过这里,因此 generation span 落点最完整。span 语义遵循 OTel GenAI
semconv + Langfuse 映射(`langfuse.observation.*`、`gen_ai.*`),见
docs/design/langfuse-observability.md。

隐私:prompt/正文只在 `LOG_CONTENT=true` 时写入 observation 的 input/output
(与既有 llm_content 日志同一开关);默认只记录模型、token 用量、延迟与错误。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import litellm
from common import langfuse
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import CompletionRequest, UsageOut
from llm_gateway.services.completion_request import (
    _content_preview,
    _provider,
    _roles,
)

_TRACER_NAME = "llm_gateway.completions"
_STATUS_LIMIT = 500


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def ingested_cost(model: str, usage: UsageOut) -> dict[str, float] | None:
    """按 litellm 内置价目表算 USD 成本,未知模型返回 None。

    走 ingested cost 而不是让 Langfuse 推断:云端托管价目表不含 deepseek
    (2026-11 实测),而 litellm 随网关依赖升级自带价目,不会漏模型。
    价格不在本仓库硬编码;未映射的自定义/BYOK 模型返回 None,由 Langfuse
    按自己的价目表推断(有则算、无则空),不会因为算价失败影响请求。
    """
    try:
        info = litellm.get_model_info(model)
    except Exception:  # noqa: BLE001 - 未映射模型是正常情况,不应影响请求
        return None
    input_price = info.get("input_cost_per_token") or 0.0
    output_price = info.get("output_cost_per_token") or 0.0
    if not input_price and not output_price:
        return None
    prompt_cost = usage.prompt_tokens * float(input_price)
    completion_cost = usage.completion_tokens * float(output_price)
    return {
        "input": prompt_cost,
        "output": completion_cost,
        "total": prompt_cost + completion_cost,
    }


def _input_payload(
    settings: Settings, data: CompletionRequest
) -> list[dict[str, str]] | None:
    if not settings.log_content:
        return None
    return [
        {"role": str(message.role), "content": _content_preview(message.content)}
        for message in data.messages
    ]


@contextmanager
def generation_span(
    settings: Settings,
    data: CompletionRequest,
    model: str,
    *,
    stream: bool,
) -> Iterator[Span]:
    """打开 generation span:一个 span 覆盖整个逻辑调用(含 fallback 重试)。"""
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(f"chat {model}") as span:
        attributes = langfuse.observation_attributes(
            observation_type="generation",
            model=model,
            input=_input_payload(settings, data),
            metadata={"stream": stream, "roles": _roles(data)},
        )
        attributes.update(
            {
                langfuse.GEN_AI_OPERATION_NAME: "chat",
                langfuse.GEN_AI_REQUEST_MODEL: model,
                langfuse.GEN_AI_PROVIDER_NAME: _provider(model),
            }
        )
        langfuse.apply_attributes(span, attributes)
        yield span


def mark_completion_start(span: Span) -> None:
    """首个增量到达的时间(Langfuse 用它算 TTFT);幂等由调用方保证。"""
    langfuse.apply_attributes(span, {langfuse.OBSERVATION_COMPLETION_START: _iso_now()})


def finish_generation(
    span: Span,
    *,
    settings: Settings,
    model: str,
    usage: UsageOut | None,
    content: str | None = None,
    fallbacks: int = 0,
) -> None:
    """收尾:响应模型、token 用量、成本(ingested,litellm 价目)与可选正文。"""
    attributes: dict[str, Any] = {
        langfuse.GEN_AI_RESPONSE_MODEL: model,
        f"{langfuse.OBSERVATION_METADATA_PREFIX}fallback_count": str(fallbacks),
    }
    if usage is not None:
        attributes.update(
            langfuse.gen_ai_usage_attributes(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            )
        )
        cost = ingested_cost(model, usage)
        if cost is not None:
            attributes[langfuse.OBSERVATION_COST] = langfuse.json_value(cost)
    if settings.log_content and content is not None:
        attributes[langfuse.OBSERVATION_OUTPUT] = langfuse.json_value(content)
    langfuse.apply_attributes(span, attributes)


def record_error(span: Span, exc: BaseException) -> None:
    """异常收尾:异常事件 + Langfuse observation level/status message。"""
    span.record_exception(exc)
    message = f"{type(exc).__name__}: {exc}"[:_STATUS_LIMIT]
    span.set_status(Status(StatusCode.ERROR, message))
    langfuse.apply_attributes(
        span,
        {
            langfuse.OBSERVATION_LEVEL: "ERROR",
            langfuse.OBSERVATION_STATUS_MESSAGE: message,
        },
    )


def record_stream_error(span: Span, message: str) -> None:
    """流式错误片(HTTP 头已发出,无法抛异常)对应的 observation 状态。"""
    langfuse.apply_attributes(
        span,
        {
            langfuse.OBSERVATION_LEVEL: "ERROR",
            langfuse.OBSERVATION_STATUS_MESSAGE: message[:_STATUS_LIMIT],
        },
    )
