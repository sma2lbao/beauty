"""工具执行的 trace:一次 tool_call 对应一个 `execute_tool {name}` observation。

字段与 `tool_calls` 审计表对齐(风险、决策、耗时、结果大小、路径、补丁行数),
trace 后端(Langfuse)因此能按工具聚合成功率与耗时,而不必翻审计表。

隐私:默认只写元数据,不写参数与结果原文。`TOOL__LOG_CONTENT=true` 时才把
截断后的参数写进 observation.input;结果原文始终不写(可能很大且含用户数据)。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from common import langfuse
from common.tools import ToolDescriptor
from opentelemetry import trace
from opentelemetry.trace import Span, Tracer

from tool_service.services.execution.models import ExecutionContext

_TRACER_NAME = "tool_service.execution"
_MAX_INPUT_CHARS = 2000
#: gen_ai 语义约定里工具调用的类型(function / extension)。
_TOOL_TYPE = "function"
#: 从审计列搬进 observation.metadata 的字段(与 tool_calls 列同名)。
_METADATA_KEYS = (
    "tool_version",
    "risk",
    "decision",
    "ok",
    "error_code",
    "duration_ms",
    "result_chars",
    "bytes_written",
    "truncated",
    "sha256_before",
    "sha256_after",
    "sandbox_id",
    "connection_id",
    "lines_added",
    "lines_removed",
    "patch_truncated",
)


def _input_payload(
    arguments: dict[str, Any], log_content: bool
) -> dict[str, Any] | None:
    """参数原文仅在显式开启内容记录时出现,且按审计同款长度截断。"""

    if not log_content:
        return None
    raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return {"arguments": raw[:_MAX_INPUT_CHARS]}


def span_attributes(
    descriptor: ToolDescriptor,
    context: ExecutionContext,
) -> dict[str, Any]:
    """构造工具 span 的起始属性(语义约定 + Langfuse observation 字段)。"""

    attributes = langfuse.observation_attributes(
        observation_type="tool",
        metadata={
            "run_id": context.run_id,
            "conversation_id": context.conversation_id,
            "risk": str(descriptor.risk),
            "tool_version": descriptor.version,
            "approved": context.approved,
        },
    )
    attributes.update(
        {
            langfuse.GEN_AI_OPERATION_NAME: "execute_tool",
            langfuse.GEN_AI_TOOL_NAME: descriptor.name,
            langfuse.GEN_AI_TOOL_CALL_ID: context.tool_call_id,
            "gen_ai.tool.type": _TOOL_TYPE,
        }
    )
    return attributes


@contextmanager
def tool_span(
    descriptor: ToolDescriptor,
    context: ExecutionContext,
    *,
    arguments: dict[str, Any] | None = None,
    log_content: bool = False,
    tracer: Tracer | None = None,
) -> Iterator[Span]:
    """开一个工具执行 span;异常时标 ERROR 并原样抛出(由调用方回错误信封)。"""

    tracer = tracer or trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(f"execute_tool {descriptor.name}") as span:
        attributes = span_attributes(descriptor, context)
        payload = _input_payload(arguments or {}, log_content)
        if payload is not None:
            attributes[langfuse.OBSERVATION_INPUT] = langfuse.json_value(payload)
        langfuse.apply_attributes(span, attributes)
        try:
            yield span
        except Exception as exc:  # 保留原异常,只补观察级别
            langfuse.apply_attributes(
                span,
                langfuse.observation_attributes(
                    level="ERROR",
                    status_message=f"{type(exc).__name__}: {exc}"[:500],
                ),
            )
            raise


def finish_tool_span(
    span: Span,
    *,
    columns: dict[str, Any],
    ok: bool,
) -> None:
    """收尾:把审计列搬进 observation.metadata,失败降为 WARNING。"""

    metadata = {
        key: columns[key] for key in _METADATA_KEYS if columns.get(key) is not None
    }
    paths = columns.get("paths")
    if isinstance(paths, str) and paths:
        try:
            metadata["paths"] = json.loads(paths)
        except ValueError:
            metadata["paths"] = paths
    attributes = langfuse.observation_attributes(
        metadata=metadata,
        level=None if ok else "WARNING",
    )
    langfuse.apply_attributes(span, attributes)
