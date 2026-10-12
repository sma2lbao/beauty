"""run 执行图组装:plan → 审批闸 → execute → verify(返工≤3)→ deliver(§6)。

checkpointer:阶段 A 用 InMemorySaver(进程内,测试即隔离);
接 compose 的 postgres 后切 langgraph-checkpoint-postgres,图线程跨进程可恢复。
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_orchestrator.graph.nodes import (
    approval_gate_node,
    deliver_node,
    execute_node,
    plan_node,
    verify_node,
)
from agent_orchestrator.graph.state import RunGraphState

MAX_REWORK_ROUNDS = 3


def route_after_gate(state: RunGraphState) -> str:
    """拒绝 → 图终结(blocked);放行 → 执行。"""
    if state.get("final_status") == "blocked":
        return END
    return "execute"


def route_after_verify(state: RunGraphState) -> str:
    """验收有未通过项且未达返工上限 → 回 execute(§6.1);否则交付。"""
    verified = state.get("verified") or {}
    rounds = state.get("rounds", 0)
    if verified and not all(verified.values()) and rounds < MAX_REWORK_ROUNDS:
        return "execute"
    return "deliver"


def build_run_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    checkpointer = checkpointer or InMemorySaver()
    builder = StateGraph(RunGraphState)
    builder.add_node("plan", plan_node)
    builder.add_node("approval_gate", approval_gate_node)
    builder.add_node("execute", execute_node)
    builder.add_node("verify", verify_node)
    builder.add_node("deliver", deliver_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate", route_after_gate, {"execute": "execute", END: END}
    )
    builder.add_edge("execute", "verify")
    builder.add_conditional_edges(
        "verify", route_after_verify, {"execute": "execute", "deliver": "deliver"}
    )
    builder.add_edge("deliver", END)
    return builder.compile(checkpointer=checkpointer)
