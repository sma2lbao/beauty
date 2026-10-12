"""run 执行图组装:plan → 审批闸 → execute → verify(返工≤3)→ deliver(§6)。

checkpointer:阶段 A 用 InMemorySaver(进程内,测试即隔离);
接 compose 的 postgres 后切 langgraph-checkpoint-postgres,图线程跨进程可恢复。
llm:plan/execute 的模型客户端(架构 §5 唯一出口经 llm-gateway);
None 回退确定性桩——dev/测试零外部依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_orchestrator.graph.nodes import (
    approval_gate_node,
    deliver_node,
    fail_node,
    make_execute_node,
    make_plan_node,
    make_verify_node,
)
from agent_orchestrator.graph.state import RunGraphState

if TYPE_CHECKING:
    from agent_orchestrator.services.llm import LLMClient

MAX_REWORK_ROUNDS = 3


def route_after_gate(state: RunGraphState) -> str:
    """拒绝 → 图终结(blocked);放行 → 执行。"""
    if state.get("final_status") == "blocked":
        return END
    return "execute"


def route_after_verify(state: RunGraphState) -> str:
    """验收全过 → deliver;有未过且未达返工上限 → execute;耗尽 → fail(§6.1)。"""
    verified = state.get("verified") or {}
    rounds = state.get("rounds", 0)
    if not verified or all(verified.values()):
        return "deliver"
    if rounds < MAX_REWORK_ROUNDS:
        return "execute"
    return "fail"


def build_run_graph(
    checkpointer: BaseCheckpointSaver | None = None,
    llm: LLMClient | None = None,
) -> CompiledStateGraph:
    checkpointer = checkpointer or InMemorySaver()
    builder = StateGraph(RunGraphState)
    builder.add_node("plan", make_plan_node(llm))
    builder.add_node("approval_gate", approval_gate_node)
    builder.add_node("execute", make_execute_node(llm))
    builder.add_node("verify", make_verify_node(llm))
    builder.add_node("deliver", deliver_node)
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate", route_after_gate, {"execute": "execute", END: END}
    )
    builder.add_edge("execute", "verify")
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {"execute": "execute", "deliver": "deliver", "fail": "fail"},
    )
    builder.add_edge("deliver", END)
    builder.add_edge("fail", END)
    return builder.compile(checkpointer=checkpointer)
