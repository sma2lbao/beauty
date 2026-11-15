"""run 审批/确认/拒绝与图结果落库(自 run_execution.py 拆出)。

拆分说明:run.tool.called.v1 的 payload 裁剪在 run_execution_tool_events,
本模块保留门面重导出,并承载审批决定、恢复执行与结果投影。

包化拆分(W5 出口):support/graph_result/decisions;
本 __init__ 是门面,保持原导入路径与名字不变。

"""

from __future__ import annotations

from agent_orchestrator.services.run_execution_tool_events import (
    MAX_TOOL_EVENT_BYTES as MAX_TOOL_EVENT_BYTES,
)
from agent_orchestrator.services.run_execution_tool_events import (
    _tool_event_payload as _tool_event_payload,
)

from .decisions import (
    _apply_grants,
    _pending_write_tools,
    approve_run,
    confirm_run,
    reject_run,
)
from .graph_result import (
    _apply_graph_result,
)
from .support import (
    _GRAPH_PORTS,
    _TERMINAL_OR_CANCELLED,
    _ApprovalAdapter,
    _config,
    _graph_ports,
    _interrupt_payload,
    _thread_id,
    _tool_context,
)

__all__ = [
    "_TERMINAL_OR_CANCELLED",
    "_ApprovalAdapter",
    "_graph_ports",
    "_GRAPH_PORTS",
    "_tool_context",
    "_thread_id",
    "_config",
    "_interrupt_payload",
    "_apply_graph_result",
    "approve_run",
    "_pending_write_tools",
    "_apply_grants",
    "confirm_run",
    "reject_run",
    "MAX_TOOL_EVENT_BYTES",
    "_tool_event_payload",
    "Mapping",
    "Any",
    "ArtifactStore",
    "get_logger",
    "CompiledStateGraph",
    "Command",
    "AsyncSession",
    "metrics",
    "delta",
    "ItemProjector",
    "ApprovalDecision",
    "GraphPorts",
    "GRAPH_RUNTIME_KEY",
    "GraphRuntime",
    "StepProjector",
    "Run",
    "QUESTION_REQUIRED_EVENT",
    "TOOL_CALLED_EVENT",
    "RunEventRepository",
    "RunStatus",
    "approval_service",
    "hooks_service",
    "jobs_service",
    "memory_extraction",
    "run_scores",
    "usage_service",
    "FileDownloader",
    "bind_run_llm",
    "_deliver_artifacts",
    "_persist_artifact",
    "_append_assistant_message",
    "_delta_stream",
    "_fail_run",
    "_item_stream",
    "_step_stream",
    "append_event",
    "is_superseded",
    "MAX_EVENT_ARGUMENTS_CHARS",
    "MAX_EVENT_PATH_CHARS",
    "MAX_EVENT_PATHS",
    "MAX_EVENT_SHA_CHARS",
    "_event_int",
    "_event_paths",
    "_event_sha",
    "_event_text",
    "_fit_tool_event",
    "_payload_bytes",
    "_consume_instructions",
    "mark_started",
    "require_run",
    "require_transition",
    "_usage_boundary",
    "run_trace",
    "ToolContext",
    "bind_tool_context",
    "ToolRegistry",
    "ToolRisk",
    "Workspace",
]
