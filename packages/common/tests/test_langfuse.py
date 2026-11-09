"""Langfuse 接入:导出目标、属性映射、baggage 传播(纯 OTel,无 SDK 依赖)。

OTel 的全局 provider 一个进程只能 set 一次,这里不碰全局 provider,直接
构造 TracerProvider 验证处理器与属性语义。
"""

from __future__ import annotations

import base64
import json
from typing import Any

from opentelemetry import baggage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from common import langfuse, tracing
from common.config import BaseAppSettings


class LangfuseSettings(BaseAppSettings):
    model_config = {**BaseAppSettings.model_config, "env_prefix": "TEST_LANGFUSE__"}


def _settings(**overrides: Any) -> LangfuseSettings:
    values: dict[str, Any] = {
        "service_name": "svc-langfuse",
        "langfuse_enabled": True,
        "langfuse_host": "http://langfuse.test:3000/",
        "langfuse_public_key": "pk-lf-test",
        "langfuse_secret_key": "sk-lf-test",
        "trace_sample_ratio": 1.0,
    }
    values.update(overrides)
    return LangfuseSettings(**values)


def _provider(*extra: Any) -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    for processor in extra:
        provider.add_span_processor(processor)
    return provider, exporter


def test_export_target_requires_all_credentials() -> None:
    assert langfuse.export_target(_settings()) is not None
    assert langfuse.export_target(_settings(langfuse_public_key=None)) is None
    assert langfuse.export_target(_settings(langfuse_secret_key=None)) is None
    assert langfuse.export_target(_settings(langfuse_enabled=False)) is None


def test_export_target_derives_endpoint_and_basic_auth() -> None:
    endpoint, headers = langfuse.export_target(_settings())  # type: ignore[misc]
    assert endpoint == "http://langfuse.test:3000/api/public/otel/v1/traces"
    token = base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert headers["Authorization"] == f"Basic {token}"
    assert headers["x-langfuse-ingestion-version"] == "4"


def test_trace_attributes_map_to_langfuse_keys() -> None:
    attributes = langfuse.trace_attributes(
        trace_name="run:abc",
        session_id="conv-1",
        user_id="acct-1",
        tags=["agent", "run"],
        release="sha-1",
        metadata={"workspace": {"id": "ws-1"}},
    )
    assert attributes["langfuse.trace.name"] == "run:abc"
    assert attributes["langfuse.session.id"] == "conv-1"
    assert attributes["langfuse.user.id"] == "acct-1"
    assert attributes["langfuse.trace.tags"] == ["agent", "run"]
    assert attributes["langfuse.release"] == "sha-1"
    assert json.loads(attributes["langfuse.trace.metadata.workspace"]) == {"id": "ws-1"}
    # 未提供的属性不写 None(OTel 不接受 None 值)
    assert "langfuse.version" not in attributes


def test_observation_attributes_map_to_langfuse_keys() -> None:
    attributes = langfuse.observation_attributes(
        observation_type="generation",
        input=[{"role": "user", "content": "hi"}],
        output="hello",
        model="gpt-x",
        usage={"input": 3, "output": 2, "total": 5},
        cost=0.00042,
        level="WARNING",
        status_message="slow",
        completion_start_time="2026-11-09T20:00:00Z",
        metadata={"route": "primary"},
    )
    assert attributes["langfuse.observation.type"] == "generation"
    assert json.loads(attributes["langfuse.observation.input"]) == [
        {"role": "user", "content": "hi"}
    ]
    assert json.loads(attributes["langfuse.observation.output"]) == "hello"
    assert attributes["langfuse.observation.model.name"] == "gpt-x"
    assert json.loads(attributes["langfuse.observation.usage_details"]) == {
        "input": 3,
        "output": 2,
        "total": 5,
    }
    assert json.loads(attributes["langfuse.observation.cost_details"]) == {
        "total": 0.00042
    }
    assert attributes["langfuse.observation.level"] == "WARNING"
    assert attributes["langfuse.observation.status_message"] == "slow"
    assert (
        attributes["langfuse.observation.completion_start_time"]
        == "2026-11-09T20:00:00Z"
    )
    assert json.loads(attributes["langfuse.observation.metadata.route"]) == "primary"


def test_observation_type_rejects_unknown_value() -> None:
    try:
        langfuse.observation_attributes(observation_type="unknown")
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("expected ValueError")


def test_gen_ai_usage_attributes_drop_missing_values() -> None:
    attributes = langfuse.gen_ai_usage_attributes(
        input_tokens=10, output_tokens=4, total_tokens=14, cache_read_tokens=6
    )
    assert attributes == {
        "gen_ai.usage.input_tokens": 10,
        "gen_ai.usage.output_tokens": 4,
        "gen_ai.usage.total_tokens": 14,
        "gen_ai.usage.cache_read_input_tokens": 6,
    }


def test_langfuse_scope_propagates_trace_attributes_to_child_spans() -> None:
    provider, exporter = _provider(langfuse.LangfuseBaggageSpanProcessor())
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("run") as root:
        with langfuse.langfuse_scope(
            trace_name="run:abc",
            session_id="conv-1",
            user_id="acct-1",
            tags=["agent"],
        ):
            assert baggage.get_all()["langfuse.session.id"] == "conv-1"
            with tracer.start_as_current_span("step") as child:
                assert child.attributes["langfuse.trace.name"] == "run:abc"
                # OTel 读回序列属性时是 tuple
                assert child.attributes["langfuse.trace.tags"] == ("agent",)
                assert child.attributes["langfuse.user.id"] == "acct-1"
        assert root.attributes["langfuse.session.id"] == "conv-1"
    # 作用域退出后 baggage 必须清空,避免同进程后续任务串味
    assert baggage.get_all() == {}
    assert len(exporter.get_finished_spans()) == 2


def test_baggage_processor_ignores_foreign_and_malformed_baggage() -> None:
    from opentelemetry import context

    from common.langfuse import TRACE_TAGS

    processor = langfuse.LangfuseBaggageSpanProcessor()
    ctx = context.get_current()
    ctx = baggage.set_baggage("other.key", "v", context=ctx)
    ctx = baggage.set_baggage(TRACE_TAGS, "not-json", context=ctx)
    ctx = baggage.set_baggage("langfuse.release", "sha-1", context=ctx)
    provider, _ = _provider()
    tracer = provider.get_tracer("test")
    token = context.attach(ctx)
    try:
        with tracer.start_as_current_span("span") as span:
            processor.on_start(span, context.get_current())
            assert "other.key" not in span.attributes
            assert "langfuse.trace.tags" not in span.attributes
            assert span.attributes["langfuse.release"] == "sha-1"
    finally:
        context.detach(token)


def test_build_provider_prefers_langfuse_over_generic_otlp() -> None:
    settings = _settings(otlp_endpoint="http://collector:4318/v1/traces")
    provider = tracing._build_provider(settings, None)  # noqa: SLF001
    processors = provider._active_span_processor._span_processors  # noqa: SLF001
    exporters = [
        processor.span_exporter  # type: ignore[attr-defined]
        for processor in processors
        if hasattr(processor, "span_exporter")
    ]
    assert len(exporters) == 1
    assert exporters[0]._endpoint == (  # noqa: SLF001
        "http://langfuse.test:3000/api/public/otel/v1/traces"
    )
    assert any(
        isinstance(processor, langfuse.LangfuseBaggageSpanProcessor)
        for processor in processors
    )


def test_build_provider_keeps_generic_otlp_without_langfuse() -> None:
    settings = _settings(
        langfuse_enabled=False, otlp_endpoint="http://collector:4318/v1/traces"
    )
    provider = tracing._build_provider(settings, None)  # noqa: SLF001
    processors = provider._active_span_processor._span_processors  # noqa: SLF001
    assert not any(
        isinstance(processor, langfuse.LangfuseBaggageSpanProcessor)
        for processor in processors
    )
