"""人工闸门节点:plan 审批闸与危险操作确认闸(设计 §6.2)。

自 graph/nodes.py 拆出,interrupt 载荷与返回增量不变。
"""

from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from agent_orchestrator.graph.nodes.toolkit import _has_dangerous_tools
from agent_orchestrator.graph.ports import ToolExtrasFactory, ToolkitFactory
from agent_orchestrator.graph.runtime import runtime_from_config
from agent_orchestrator.graph.state import RunGraphState


def make_approval_gate_node(
    toolkit_factory: ToolkitFactory | None = None,
    extras_factory: ToolExtrasFactory | None = None,
    *,
    auto_approve: bool = True,
):
    """审批闸门工厂:plan 之后按工具风险决定"自动放行"还是"中断等人"(§6.2)。

    - 无 DANGER 工具:不中断,标记 auto_approved 直接进 execute——没有危险动作
      可做,逐步点审批只会把 agent 退化成人工点击机;
    - 有 DANGER 工具(未信任 MCP 的对外/不可逆操作):interrupt 等 approve/reject;
      放行时把 resume 载荷里的追加指令(§6.5)并入 state,后续节点遵循。
    """

    async def approval_gate_node(state: RunGraphState, config=None) -> dict:
        runtime = runtime_from_config(config)
        if auto_approve and not await _has_dangerous_tools(
            state, toolkit_factory, extras_factory, runtime
        ):
            return {"auto_approved": True}
        # kind 是中断类型判别键(§10.3):_apply_graph_result 据此分流
        # plan/confirmation/question;旧前端只读其余字段,语义不变。
        answer = interrupt(
            {"kind": "plan", "plan_markdown": state.get("plan_markdown", "")}
        )
        if answer.get("action") != "approve":
            return {
                "final_status": "blocked",
                "status_reason": answer.get("reason") or "用户拒绝",
            }
        return _gate_update(answer)

    return approval_gate_node


def _gate_update(answer: dict[str, Any]) -> dict[str, Any]:
    """闸门共用:把人工操作时消费掉的追加指令并入 state(§6.5)。"""
    instructions = answer.get("instructions") or []
    return {"extra_instructions": instructions} if instructions else {}


def confirm_gate_node(state: RunGraphState) -> dict:
    """危险操作确认闸(§6.2):execute 收集到危险操作后中断,等人工放行/拒绝。

    注意:interrupt 之前不得有任何副作用——LangGraph 恢复时会重放本节点,
    副作用逻辑必须留在 tool_exec 里。
    """
    pending = state.get("pending_writes") or []
    answer = interrupt({"kind": "confirmation", "pending_writes": pending})
    gate = _gate_update(answer)
    if answer.get("action") != "approve":
        reason = answer.get("reason") or "用户拒绝危险操作"
        return {
            **gate,
            "writes_approved": False,
            "pending_writes": [],
            "results": [f"危险操作被拒绝:{reason}"],
        }
    return {**gate, "writes_approved": True}
