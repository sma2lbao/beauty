"""run 执行图组装(§6):

    plan → 审批闸 → execute(工具循环)
      ├─ 有写操作 → 确认闸(interrupt)→ tool_exec → 回 execute
      └─ 无写操作 → verify(返工≤3)→ deliver / fail

checkpointer:生产 Postgres(容器/compose 起的库)用 AsyncPostgresSaver,
图线程跨进程可恢复;SQLite/dev 回退 InMemorySaver(进程内,测试即隔离)。
llm:模型客户端(架构 §5 唯一出口经 llm-gateway);
toolkit_factory:租户 → 工具注册表(§8,execute 的 function-calling 用);
两者为 None 时回退确定性桩——dev/测试零外部依赖。
"""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent_orchestrator.graph.nodes import (
    confirm_gate_node,
    fail_node,
    make_approval_gate_node,
    make_deliver_node,
    make_execute_node,
    make_plan_node,
    make_tool_exec_node,
    make_verify_node,
)
from agent_orchestrator.graph.ports import (
    LLMPort,
    ToolExtrasFactory,
    ToolkitFactory,
)
from agent_orchestrator.graph.state import RunGraphState

MAX_REWORK_ROUNDS = 3


def route_after_gate(state: RunGraphState) -> str:
    """拒绝 → 图终结(blocked);放行 → 执行。"""
    if state.get("final_status") == "blocked":
        return END
    return "execute"


def route_after_execute(state: RunGraphState) -> str:
    """execute 收集到写操作 → 确认闸;否则进入验收(§6.2)。"""
    return "confirm_gate" if state.get("pending_writes") else "verify"


def route_after_confirm(state: RunGraphState) -> str:
    """放行 → 执行写操作;拒绝 → 直接验收(§6.2)。"""
    return "tool_exec" if state.get("writes_approved") else "verify"


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
    llm: LLMPort | None = None,
    toolkit_factory: ToolkitFactory | None = None,
    mcp_tools_factory: ToolExtrasFactory | None = None,
    *,
    plan_auto_approve: bool = True,
) -> CompiledStateGraph:
    checkpointer = checkpointer or InMemorySaver()
    builder = StateGraph(RunGraphState)
    builder.add_node("plan", make_plan_node(llm))
    builder.add_node(
        "approval_gate",
        make_approval_gate_node(
            toolkit_factory, mcp_tools_factory, auto_approve=plan_auto_approve
        ),
    )
    builder.add_node(
        "execute",
        make_execute_node(llm, toolkit_factory, mcp_tools_factory),
    )
    builder.add_node("confirm_gate", confirm_gate_node)
    builder.add_node(
        "tool_exec",
        make_tool_exec_node(toolkit_factory, mcp_tools_factory),
    )
    builder.add_node("verify", make_verify_node(llm))
    builder.add_node("deliver", make_deliver_node(llm))
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate", route_after_gate, {"execute": "execute", END: END}
    )
    builder.add_conditional_edges(
        "execute",
        route_after_execute,
        {"confirm_gate": "confirm_gate", "verify": "verify"},
    )
    builder.add_conditional_edges(
        "confirm_gate",
        route_after_confirm,
        {"tool_exec": "tool_exec", "verify": "verify"},
    )
    builder.add_edge("tool_exec", "execute")
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {"execute": "execute", "deliver": "deliver", "fail": "fail"},
    )
    builder.add_edge("deliver", END)
    builder.add_edge("fail", END)
    return builder.compile(checkpointer=checkpointer)
