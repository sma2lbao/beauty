"""工具执行 span:语义属性、隐私开关、审计元数据与失败级别。"""

from __future__ import annotations

import json

import pytest
from common import langfuse
from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from tests.execution_support import execution_context
from tool_service.services.execution import finish_tool_span, tool_span

DESCRIPTOR = ToolDescriptor(
    name="read",
    description="读取文件",
    input_schema={"type": "object"},
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.SANDBOX,
    version="2",
)

COLUMNS = {
    "tool_name": "read",
    "tool_version": "2",
    "risk": "read",
    "decision": "auto",
    "ok": True,
    "error_code": None,
    "duration_ms": 12,
    "result_chars": 345,
    "bytes_written": 0,
    "truncated": False,
    "paths": json.dumps(["/workspace/a.txt"]),
    "sha256_before": "before",
    "sha256_after": "after",
    "sandbox_id": "sandbox-1",
    "connection_id": None,
    "lines_added": 0,
    "lines_removed": 0,
    "patch_truncated": False,
}


@pytest.fixture()
def tracer_and_exporter():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _span(exporter: InMemorySpanExporter):
    (span,) = exporter.get_finished_spans()
    return span


def test_span_carries_semantic_and_langfuse_attributes(tracer_and_exporter):
    tracer, exporter = tracer_and_exporter
    with tool_span(DESCRIPTOR, execution_context(run_id="run-1"), tracer=tracer):
        pass
    span = _span(exporter)
    assert span.name == "execute_tool read"
    attributes = span.attributes
    assert attributes[langfuse.OBSERVATION_TYPE] == "tool"
    assert attributes[langfuse.GEN_AI_OPERATION_NAME] == "execute_tool"
    assert attributes[langfuse.GEN_AI_TOOL_NAME] == "read"
    assert attributes[langfuse.GEN_AI_TOOL_CALL_ID] == "call-1"
    assert attributes["gen_ai.tool.type"] == "function"
    assert attributes[f"{langfuse.OBSERVATION_METADATA_PREFIX}run_id"] == '"run-1"'
    assert attributes[f"{langfuse.OBSERVATION_METADATA_PREFIX}risk"] == '"read"'
    # 默认不记录参数原文
    assert langfuse.OBSERVATION_INPUT not in attributes


def test_log_content_records_truncated_arguments(tracer_and_exporter):
    tracer, exporter = tracer_and_exporter
    arguments = {"path": "/workspace/a.txt", "limit": 20}
    with tool_span(
        DESCRIPTOR,
        execution_context(),
        arguments=arguments,
        log_content=True,
        tracer=tracer,
    ):
        pass
    attributes = _span(exporter).attributes
    payload = json.loads(attributes[langfuse.OBSERVATION_INPUT])
    assert json.loads(payload["arguments"]) == arguments
    assert len(payload["arguments"]) <= 2000


def test_finish_moves_audit_columns_into_metadata(tracer_and_exporter):
    tracer, exporter = tracer_and_exporter
    with tool_span(DESCRIPTOR, execution_context(), tracer=tracer) as span:
        finish_tool_span(span, columns=COLUMNS, ok=True)
    attributes = _span(exporter).attributes
    prefix = langfuse.OBSERVATION_METADATA_PREFIX
    assert attributes[f"{prefix}duration_ms"] == "12"
    assert attributes[f"{prefix}result_chars"] == "345"
    assert attributes[f"{prefix}truncated"] == "false"
    assert attributes[f"{prefix}paths"] == '["/workspace/a.txt"]'
    assert attributes[f"{prefix}sandbox_id"] == '"sandbox-1"'
    # ok=true 不降级;空值不写空 metadata
    assert langfuse.OBSERVATION_LEVEL not in attributes
    assert f"{prefix}error_code" not in attributes
    assert f"{prefix}connection_id" not in attributes


def test_finish_marks_failure_as_warning(tracer_and_exporter):
    tracer, exporter = tracer_and_exporter
    failed = {**COLUMNS, "ok": False, "error_code": "tool.exec_failed"}
    with tool_span(DESCRIPTOR, execution_context(), tracer=tracer) as span:
        finish_tool_span(span, columns=failed, ok=False)
    attributes = _span(exporter).attributes
    assert attributes[langfuse.OBSERVATION_LEVEL] == "WARNING"
    assert (
        attributes[f"{langfuse.OBSERVATION_METADATA_PREFIX}error_code"]
        == '"tool.exec_failed"'
    )


def test_exception_marks_error_and_propagates(tracer_and_exporter):
    tracer, exporter = tracer_and_exporter
    with (
        pytest.raises(RuntimeError, match="sandbox down"),
        tool_span(DESCRIPTOR, execution_context(), tracer=tracer),
    ):
        raise RuntimeError("sandbox down")
    span = _span(exporter)
    assert span.attributes[langfuse.OBSERVATION_LEVEL] == "ERROR"
    assert "sandbox down" in span.attributes[langfuse.OBSERVATION_STATUS_MESSAGE]
    assert span.status.status_code is StatusCode.ERROR
