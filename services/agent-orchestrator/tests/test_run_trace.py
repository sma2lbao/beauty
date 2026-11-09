"""run 根 span:trace 级属性、可过滤 metadata、父子关系与 trace_id 回写。"""

from __future__ import annotations

from types import SimpleNamespace

from common import langfuse
from opentelemetry import baggage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

from agent_orchestrator.services.run_trace import run_trace


def _run(**overrides):
    """每次给全新对象,避免用例之间共享 trace_id。"""

    values = {
        "id": "run-1",
        "account_id": "account-1",
        "conversation_id": "conversation-1",
        "attempts": 2,
        "agent_name": "planner",
        "workspace_id": "workspace-1",
        "parent_run_id": None,
        "trace_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _tracer(sampler=None):
    provider = TracerProvider() if sampler is None else TracerProvider(sampler=sampler)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_run_span_sets_trace_attributes_and_agent_metadata():
    run = _run()
    tracer, exporter = _tracer()
    with run_trace(run, tracer=tracer):
        pass
    (span,) = exporter.get_finished_spans()
    assert span.name == "run run-1"
    attributes = span.attributes
    assert attributes[langfuse.TRACE_NAME] == "run run-1"
    assert attributes[langfuse.USER_ID] == "account-1"
    assert attributes[langfuse.SESSION_ID] == "conversation-1"
    assert attributes[langfuse.OBSERVATION_TYPE] == "agent"
    assert attributes[langfuse.GEN_AI_AGENT_NAME] == "planner"
    assert attributes[langfuse.GEN_AI_CONVERSATION_ID] == "conversation-1"
    prefix = langfuse.OBSERVATION_METADATA_PREFIX
    assert attributes[f"{prefix}run_id"] == '"run-1"'
    assert attributes[f"{prefix}attempts"] == "2"
    # None 值不进 metadata(否则 Langfuse 里全是空键)
    assert f"{prefix}parent_run_id" not in attributes
    # 回写的 trace_id 与导出的 span 一致,且是 32 位十六进制(Langfuse URL 格式)
    assert run.trace_id == f"{span.context.trace_id:032x}"


def test_run_span_keeps_trace_id_when_sampling_drops_span():
    """采样关闭时 span 上下文无效,不能把 0 写进 run.trace_id。"""

    run = _run(trace_id="0123456789abcdef0123456789abcdef")
    tracer, _ = _tracer(sampler=ALWAYS_OFF)
    with run_trace(run, tracer=tracer):
        pass
    assert run.trace_id == "0123456789abcdef0123456789abcdef"


def test_run_span_propagates_trace_attributes_through_baggage():
    run = _run()
    tracer, exporter = _tracer()
    with run_trace(run, tracer=tracer):
        assert baggage.get_baggage(langfuse.SESSION_ID) == "conversation-1"
        child = tracer.start_span("child")
        child.end()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["run run-1"].parent is None
    assert spans["child"].parent.span_id == spans["run run-1"].context.span_id


def test_run_span_nests_under_existing_parent():
    run = _run()
    tracer, exporter = _tracer()
    with (
        tracer.start_as_current_span("POST /api/v1/runs") as parent,
        run_trace(run, tracer=tracer),
    ):
        pass
    spans = {span.name: span for span in exporter.get_finished_spans()}
    parent_span = spans["POST /api/v1/runs"]
    run_span = spans["run run-1"]
    assert run_span.parent is not None
    assert run_span.parent.span_id == parent_span.context.span_id
    assert run_span.context.trace_id == parent.context.trace_id
    # 挂在请求 span 下时,深链落到整条请求 trace(含 run 与其节点)
    assert run.trace_id == f"{parent.context.trace_id:032x}"
