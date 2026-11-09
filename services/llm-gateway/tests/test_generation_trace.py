"""generation span 的 Langfuse 映射:类型、模型、用量、成本、错误与正文开关。

不碰网络:litellm 只查本地价目表,span 落 InMemorySpanExporter。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from llm_gateway.core.config import Settings
from llm_gateway.schemas.completions import CompletionRequest, UsageOut
from llm_gateway.services import generation_trace

# OTel 全局 provider 一个进程只能设一次:模块级装一次,后续用例复用 exporter。
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_PROVIDER)

DEEPSEEK = "deepseek/deepseek-chat"
UNKNOWN = "observe/observe-model"
USAGE = UsageOut(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"available_models": [], "fallback_models": []}
    values.update(overrides)
    return Settings(_env_file=None, env="dev", log_level="DEBUG", **values)


def _request(content: str = "写周报") -> CompletionRequest:
    return CompletionRequest(
        model=DEEPSEEK, messages=[{"role": "user", "content": content}]
    )


def _last() -> Any:
    return _EXPORTER.get_finished_spans()[-1]


def test_generation_span_maps_type_model_and_metadata() -> None:
    with generation_trace.generation_span(
        _settings(), _request(), DEEPSEEK, stream=True
    ):
        pass
    span = _last()
    assert span.name == f"chat {DEEPSEEK}"
    assert span.attributes["langfuse.observation.type"] == "generation"
    assert span.attributes["langfuse.observation.model.name"] == DEEPSEEK
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.attributes["gen_ai.request.model"] == DEEPSEEK
    assert span.attributes["gen_ai.provider.name"] == "deepseek"
    assert json.loads(span.attributes["langfuse.observation.metadata.stream"]) is True
    assert json.loads(span.attributes["langfuse.observation.metadata.roles"]) == "user"
    # 默认不落正文
    assert "langfuse.observation.input" not in span.attributes


def test_generation_span_records_content_only_when_enabled() -> None:
    with generation_trace.generation_span(
        _settings(log_content=True), _request("hi"), DEEPSEEK, stream=False
    ):
        pass
    span = _last()
    assert json.loads(span.attributes["langfuse.observation.input"]) == [
        {"role": "user", "content": "hi"}
    ]


def test_finish_generation_records_usage_and_ingested_cost() -> None:
    with generation_trace.generation_span(
        _settings(), _request(), DEEPSEEK, stream=False
    ) as span:
        generation_trace.finish_generation(
            span, settings=_settings(), model=DEEPSEEK, usage=USAGE
        )
    span = _last()
    # 用量走 gen_ai.usage.*(Langfuse 映射成 usageDetails);成本走 ingested cost_details
    assert span.attributes["gen_ai.usage.input_tokens"] == 1000
    assert span.attributes["gen_ai.usage.total_tokens"] == 1500
    assert span.attributes["gen_ai.usage.output_tokens"] == 500
    assert span.attributes["gen_ai.response.model"] == DEEPSEEK
    # litellm 价目:deepseek-chat input 0.28/M、output 0.42/M
    cost = json.loads(span.attributes["langfuse.observation.cost_details"])
    assert cost["input"] == pytest.approx(1000 * 2.8e-07)
    assert cost["output"] == pytest.approx(500 * 4.2e-07)
    assert cost["total"] == pytest.approx(cost["input"] + cost["output"])


def test_unknown_model_has_no_ingested_cost() -> None:
    assert generation_trace.ingested_cost(UNKNOWN, USAGE) is None
    with generation_trace.generation_span(
        _settings(), _request(), UNKNOWN, stream=False
    ) as span:
        generation_trace.finish_generation(
            span, settings=_settings(), model=UNKNOWN, usage=USAGE
        )
    assert "langfuse.observation.cost_details" not in _last().attributes


def test_mark_completion_start_sets_ttft_timestamp() -> None:
    with generation_trace.generation_span(
        _settings(), _request(), DEEPSEEK, stream=True
    ) as span:
        generation_trace.mark_completion_start(span)
    start = _last().attributes["langfuse.observation.completion_start_time"]
    assert start.endswith("+00:00")


def test_error_sets_level_and_status() -> None:
    with generation_trace.generation_span(
        _settings(), _request(), DEEPSEEK, stream=False
    ) as span:
        generation_trace.record_error(span, RuntimeError("upstream 502"))
    span = _last()
    assert span.attributes["langfuse.observation.level"] == "ERROR"
    assert "upstream 502" in span.attributes["langfuse.observation.status_message"]
    assert span.status.status_code.name == "ERROR"


def test_stream_error_records_status_message() -> None:
    with generation_trace.generation_span(
        _settings(), _request(), DEEPSEEK, stream=True
    ) as span:
        generation_trace.record_stream_error(span, "stream interrupted")
    assert _last().attributes["langfuse.observation.level"] == "ERROR"
    assert (
        _last().attributes["langfuse.observation.status_message"]
        == "stream interrupted"
    )
