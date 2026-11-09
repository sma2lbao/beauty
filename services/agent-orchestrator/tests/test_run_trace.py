"""run 根 span:trace 级属性、可过滤 metadata 与父子关系。"""

from __future__ import annotations

from types import SimpleNamespace

from common import langfuse
from opentelemetry import baggage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_orchestrator.services.run_trace import run_trace

RUN = SimpleNamespace(
    id="run-1",
    account_id="account-1",
    conversation_id="conversation-1",
    attempts=2,
    agent_name="planner",
    workspace_id="workspace-1",
    parent_run_id=None,
)


def _tracer():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_run_span_sets_trace_attributes_and_agent_metadata():
    tracer, exporter = _tracer()
    with run_trace(RUN, tracer=tracer):
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


def test_run_span_propagates_trace_attributes_through_baggage():
    tracer, exporter = _tracer()
    with run_trace(RUN, tracer=tracer):
        assert baggage.get_baggage(langfuse.SESSION_ID) == "conversation-1"
        child = tracer.start_span("child")
        child.end()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["run run-1"].parent is None
    assert spans["child"].parent.span_id == spans["run run-1"].context.span_id


def test_run_span_nests_under_existing_parent():
    tracer, exporter = _tracer()
    with (
        tracer.start_as_current_span("POST /api/v1/runs") as parent,
        run_trace(RUN, tracer=tracer),
    ):
        pass
    spans = {span.name: span for span in exporter.get_finished_spans()}
    parent_span = spans["POST /api/v1/runs"]
    run_span = spans["run run-1"]
    assert run_span.parent is not None
    assert run_span.parent.span_id == parent_span.context.span_id
    assert run_span.context.trace_id == parent.context.trace_id
