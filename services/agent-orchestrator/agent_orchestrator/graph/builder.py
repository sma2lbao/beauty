"""run 执行图组装(§6):

    plan → 审批闸 → execute(工具循环)
      ├─ 有写操作 → 确认闸(interrupt)→ tool_exec → 回 execute
      └─ 无写操作 → verify(返工≤3)→ deliver / fail

checkpointer:生产 Postgres(容器/compose 起的库)用 AsyncPostgresSaver,
图线程跨进程可恢复;SQLite/dev 回退 InMemorySaver(进程内,测试即隔离)。
llm:模型客户端(架构 §5 唯一出口经 llm-gateway);
toolkit_factory:账号 → 工具注册表(§8,execute 的 function-calling 用);
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
    make_ask_gate_node,
    make_deliver_node,
    make_execute_node,
    make_plan_node,
    make_subagent_wait_node,
    make_tool_exec_node,
    make_verify_node,
)
from agent_orchestrator.graph.ports import (
    LLMPort,
    OffloadPort,
    ToolExtrasFactory,
    ToolkitFactory,
)
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.graph.steps import instrument_node

MAX_REWORK_ROUNDS = 3


def route_after_gate(state: RunGraphState) -> str:
    """拒绝 → 图终结(blocked);放行 → 执行。"""
    if state.get("final_status") == "blocked":
        return END
    return "execute"


def route_after_execute(state: RunGraphState) -> str:
    """排序:待答问题 → 待确认写 → 待等子代理 → 验收(§10.3/§6.2/L2)。

    提问优先于写操作确认:本回合既没执行任何工具,也没收集待确认写操作
    (execute 命中 interaction 调用时其余调用一律不执行)。

    写操作确认优先于子代理等待:待确认的写操作还没拿到 tool 消息,
    如果先去等子代理,恢复后模型补全时会看到"assistant 消息里有未应答的
    tool_calls"——OpenAI 协议下直接 400。同回合已入队的子代理不会因此丢:
    execute 把回执一并写进 pending_subagents,确认闸之后(见
    route_after_confirm / route_after_tool_exec)再去等待节点。
    """
    if state.get("pending_question"):
        return "ask_gate"
    if state.get("pending_writes"):
        return "confirm_gate"
    if state.get("pending_subagents"):
        return "subagent_wait"
    return "verify"


def route_after_confirm(state: RunGraphState) -> str:
    """放行 → 执行写操作;拒绝 → 验收,但已入队的子代理仍要等待(§6.2/L2)。"""
    if state.get("writes_approved"):
        return "tool_exec"
    return "subagent_wait" if state.get("pending_subagents") else "verify"


def route_after_tool_exec(state: RunGraphState) -> str:
    """确认后的写操作跑完 → 有待等子代理就去等,否则回工具循环(L2)。

    待等子代理可能来自两处:execute 同回合已入队的那批(回执随状态带过来),
    或刚被人工确认执行的写操作本身就是 spawn_subagent。
    """
    return "subagent_wait" if state.get("pending_subagents") else "execute"


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
    question_timeout_seconds: int = 900,
    offloader: OffloadPort | None = None,
) -> CompiledStateGraph:
    checkpointer = checkpointer or InMemorySaver()
    builder = StateGraph(RunGraphState)
    builder.add_node("plan", instrument_node("plan", "plan", make_plan_node(llm)))
    builder.add_node(
        "approval_gate",
        make_approval_gate_node(
            toolkit_factory, mcp_tools_factory, auto_approve=plan_auto_approve
        ),
    )
    builder.add_node(
        "execute",
        instrument_node(
            "execute",
            "execute",
            make_execute_node(
                llm,
                toolkit_factory,
                mcp_tools_factory,
                question_timeout_seconds=question_timeout_seconds,
                offloader=offloader,
            ),
        ),
    )
    builder.add_node("ask_gate", make_ask_gate_node())
    # 异步子代理(L2):父 run 在这里 interrupt 等子代理终态,不占 worker
    builder.add_node("subagent_wait", make_subagent_wait_node())
    builder.add_node("confirm_gate", confirm_gate_node)
    builder.add_node(
        "tool_exec",
        instrument_node(
            "tool_exec",
            "execute",
            make_tool_exec_node(
                toolkit_factory, mcp_tools_factory, offloader=offloader
            ),
        ),
    )
    builder.add_node(
        "verify", instrument_node("verify", "verify", make_verify_node(llm))
    )
    builder.add_node(
        "deliver", instrument_node("deliver", "deliver", make_deliver_node(llm))
    )
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "approval_gate")
    builder.add_conditional_edges(
        "approval_gate", route_after_gate, {"execute": "execute", END: END}
    )
    builder.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            "ask_gate": "ask_gate",
            "confirm_gate": "confirm_gate",
            "subagent_wait": "subagent_wait",
            "verify": "verify",
        },
    )
    # 提问闸恢复后回 execute:答案已作为 tool-role 消息写进 tool_history,
    # 模型下一轮自然看到(而不是重新执行工具或重发提问)。
    builder.add_edge("ask_gate", "execute")
    # 子代理回执以 user 消息写进 tool_history,回 execute 继续本任务(L2)
    builder.add_edge("subagent_wait", "execute")
    builder.add_conditional_edges(
        "confirm_gate",
        route_after_confirm,
        {
            "tool_exec": "tool_exec",
            "subagent_wait": "subagent_wait",
            "verify": "verify",
        },
    )
    builder.add_conditional_edges(
        "tool_exec",
        route_after_tool_exec,
        {"subagent_wait": "subagent_wait", "execute": "execute"},
    )
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {"execute": "execute", "deliver": "deliver", "fail": "fail"},
    )
    builder.add_edge("deliver", END)
    builder.add_edge("fail", END)
    return builder.compile(checkpointer=checkpointer)
