"""tool_exec 节点:执行已人工确认的危险操作,把结果回喂同一消息流(设计 §6.2)。

自 graph/nodes.py 拆出,行为与审计字段不变。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from agent_orchestrator.graph import items
from agent_orchestrator.graph.nodes.audit import (
    DECISION_APPROVED,
    _elapsed_ms,
    _maybe_offload,
    _record,
    _tool_error,
    _tool_item_begin,
    _tool_item_denied,
    _tool_item_fail,
    _tool_item_finish,
)
from agent_orchestrator.graph.nodes.messages import (
    _execute_messages,
    _todos_state,
    _todos_update,
    _tool_message,
)
from agent_orchestrator.graph.nodes.toolkit import _load_toolkit
from agent_orchestrator.graph.ports import (
    RISK_DANGER,
    OffloadPort,
    ToolExtrasFactory,
    ToolkitFactory,
)
from agent_orchestrator.graph.runtime import runtime_from_config
from agent_orchestrator.graph.state import RunGraphState


def make_tool_exec_node(
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None = None,
    *,
    offloader: OffloadPort | None = None,
):
    """执行已确认的危险操作:结果回喂同一消息流,交回 execute 继续(§6.2)。"""

    async def tool_exec_node(state: RunGraphState, config=None) -> dict:
        runtime = runtime_from_config(config)
        pending = state.get("pending_writes") or []
        base = _execute_messages(state)
        messages = base + list(state.get("tool_history") or [])
        toolkit = await _load_toolkit(
            state.get("tenant_id", ""), toolkit_factory, extras_factory, runtime
        )
        allowed = state.get("allowed_tools")
        if toolkit is not None and allowed is not None:
            toolkit = toolkit.restricted(allowed)
        records: list[dict[str, Any]] = []
        latest_todos: list[dict[str, str]] | None = None
        for call in pending:
            name = str(call.get("name") or "")
            arguments = str(call.get("arguments") or "{}")
            call_id = str(call.get("id") or "") or None
            risk = toolkit.risk(name) if toolkit is not None else RISK_DANGER
            started = time.perf_counter()
            offload_record: Mapping[str, Any] | None = None
            if toolkit is None:
                # 装配缺失的错误信封不 offload(无内容可省,且要保持 ok=False)。
                output = _tool_error("工具未配置,写操作未执行")
                record = _record(
                    name,
                    arguments,
                    output,
                    tool_call_id=call_id,
                    risk=str(risk),
                    decision=DECISION_APPROVED,
                    duration_ms=_elapsed_ms(started),
                )
                records.append(record)
                _tool_item_denied(runtime, name, arguments, record, node="tool_exec")
            else:
                item_kind = items.classify_tool_kind(name)
                item_span: items.ItemSpan | None = None
                if name not in items.REPORTED_BY_SERVICE_TOOLS:
                    # 与 execute 分支同一口径:过程项由 services 层上报的工具
                    # 不在这里重复报同名工具项。
                    item_kind, item_span = _tool_item_begin(
                        runtime, name, arguments, node="tool_exec"
                    )
                try:
                    # 人工确认已放行:approved=True 是服务端写操作的风险凭据。
                    output = await toolkit.execute(
                        name, arguments, tool_call_id=call_id, approved=True
                    )
                except Exception as exc:
                    _tool_item_fail(runtime, item_span, name, arguments, exc)
                    raise
                update = _todos_update(name, output)
                if update is not None:
                    latest_todos = update
                output, offload_record = await _maybe_offload(offloader, name, output)
                record = _record(
                    name,
                    arguments,
                    output,
                    tool_call_id=call_id,
                    risk=str(risk),
                    decision=DECISION_APPROVED,
                    duration_ms=_elapsed_ms(started),
                    offload=offload_record,
                )
                records.append(record)
                _tool_item_finish(
                    runtime, item_kind, name, arguments, item_span, record
                )
            messages.append(_tool_message(str(call.get("id") or ""), output))
        return {
            "tool_history": messages[len(base) :],
            "pending_writes": [],
            "writes_approved": False,
            "tool_calls": records,
            "confirm_cycles": 1,
            **_todos_state(latest_todos),
        }

    return tool_exec_node
