"""导出前属性脱敏(common.redaction)的规则与处理器集成测试。

与 test_langfuse.py 一致:不碰 OTel 全局 provider,直接构造 TracerProvider,
断言 InMemorySpanExporter 里拿到的 span 属性已经清洗。
"""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from common import langfuse
from common.redaction import (
    MAX_ATTRIBUTE_CHARS,
    MAX_SEQUENCE_ITEMS,
    TRUNCATION_MARKER,
    AttributeRedactionSpanProcessor,
    sanitize_attributes,
)


def _processed(*, allow_content: bool) -> tuple[InMemorySpanExporter, Any]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        AttributeRedactionSpanProcessor(allow_content=allow_content)
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider.get_tracer("test-redaction")


def test_sensitive_keys_are_always_dropped() -> None:
    attributes = {
        "http.request.header.authorization": "Bearer sk-live-xxx",
        "http.request.header.x-api-key": "k",
        "session.token": "t",
        "db.password": "p",
        "some.secret.value": "s",
        "http.request.header.cookie": "c",
        "gen_ai.usage.input_tokens": 1000,
        "gen_ai.usage.output_tokens": 500,
        "http.response.status_code": 200,
    }

    sanitized = sanitize_attributes(attributes, allow_content=True)

    assert sanitized == {
        "gen_ai.usage.input_tokens": 1000,
        "gen_ai.usage.output_tokens": 500,
        "http.response.status_code": 200,
    }


def test_llm_content_is_gated_by_allow_content() -> None:
    attributes = {
        langfuse.OBSERVATION_INPUT: '{"messages": []}',
        langfuse.OBSERVATION_OUTPUT: "answer",
        "gen_ai.input.messages": "[]",
        "gen_ai.tool.call.arguments": "{}",
        "gen_ai.usage.total_tokens": 1500,
        "gen_ai.request.model": "deepseek/deepseek-chat",
    }

    without_content = sanitize_attributes(attributes, allow_content=False)
    assert without_content == {
        "gen_ai.usage.total_tokens": 1500,
        "gen_ai.request.model": "deepseek/deepseek-chat",
    }

    assert sanitize_attributes(attributes, allow_content=True) == attributes


def test_bodies_and_query_are_dropped_even_with_content() -> None:
    attributes = {
        "http.request.body": "{...}",
        "db.statement": "select 1",
        "url.query": "token=abc",
    }

    assert sanitize_attributes(attributes, allow_content=True) == {}


def test_urls_lose_query_and_userinfo() -> None:
    attributes = {
        "url.full": "https://user:pass@example.com/api/runs?stream_token=abc#frag",
        "http.target": "/api/runs?stream_token=abc",
    }

    assert sanitize_attributes(attributes, allow_content=False) == {
        "url.full": "https://[redacted]@example.com/api/runs",
        "http.target": "/api/runs",
    }


def test_long_values_are_truncated() -> None:
    long_text = "x" * (MAX_ATTRIBUTE_CHARS + 10)
    many = [f"item-{i}" for i in range(MAX_SEQUENCE_ITEMS + 5)]

    sanitized = sanitize_attributes(
        {"langfuse.observation.output": long_text, "list.value": many},
        allow_content=True,
    )

    assert sanitized["langfuse.observation.output"] == (
        "x" * MAX_ATTRIBUTE_CHARS + TRUNCATION_MARKER
    )
    assert len(sanitized["list.value"]) == MAX_SEQUENCE_ITEMS


def test_processor_redacts_exported_span_attributes() -> None:
    exporter, tracer = _processed(allow_content=False)
    with tracer.start_as_current_span("chat deepseek/deepseek-chat") as span:
        span.set_attribute("http.request.header.authorization", "Bearer secret")
        span.set_attribute(langfuse.OBSERVATION_INPUT, '{"messages": []}')
        span.set_attribute("gen_ai.usage.input_tokens", 1000)

    (exported,) = exporter.get_finished_spans()
    attributes = dict(exported.attributes or {})
    assert attributes == {"gen_ai.usage.input_tokens": 1000}


def test_processor_keeps_content_when_allowed() -> None:
    exporter, tracer = _processed(allow_content=True)
    with tracer.start_as_current_span("chat") as span:
        span.set_attribute(langfuse.OBSERVATION_INPUT, '{"messages": []}')
        span.set_attribute("http.request.header.cookie", "session=abc")

    (exported,) = exporter.get_finished_spans()
    assert dict(exported.attributes or {}) == {
        langfuse.OBSERVATION_INPUT: '{"messages": []}'
    }
