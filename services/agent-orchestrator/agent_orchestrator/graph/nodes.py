"""执行图节点:plan/execute/verify 接 llm-gateway(架构 §5),deliver 为桩。

约定(架构 §4 分层):节点是纯函数——只读 state、返回增量,
不碰数据库、不发事件;事务与事件在 services 层的图调用边界统一处理。

依赖注入(架构 §4 依赖倒置):节点需要的外界能力全部来自 graph.ports 的
Protocol,由 services 层在装配处通过 LangGraph invocation config 注入
(graph.runtime.GraphRuntime);因此本模块不依赖 services 包。

LLM 注入:builder 用 make_*_node(llm) 工厂装配;llm 为 None 时回退
确定性桩(dev/测试零外部依赖)。节点内 LLM 异常不捕获,由 services 层的
图调用边界统一转 failed 终态(§6.2)。

工具注入:execute 拿 toolkit_factory(租户 → 工具注册表);工具执行失败
不抛错,以错误文本回喂模型(§6.1),每次调用记入 state["tool_calls"]。
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.types import interrupt

from agent_orchestrator.graph import delta
from agent_orchestrator.graph.deliver_nodes import (
    DELIVER_SYSTEM_PROMPT as DELIVER_SYSTEM_PROMPT,
)
from agent_orchestrator.graph.deliver_nodes import (
    VERIFY_SYSTEM_PROMPT as VERIFY_SYSTEM_PROMPT,
)
from agent_orchestrator.graph.deliver_nodes import _artifact as _artifact
from agent_orchestrator.graph.deliver_nodes import _parse_verdicts as _parse_verdicts
from agent_orchestrator.graph.deliver_nodes import _stub_deliver as _stub_deliver
from agent_orchestrator.graph.deliver_nodes import fail_node as fail_node
from agent_orchestrator.graph.deliver_nodes import (
    make_deliver_node as make_deliver_node,
)
from agent_orchestrator.graph.deliver_nodes import make_verify_node as make_verify_node
from agent_orchestrator.graph.plan_nodes import PLAN_SYSTEM_PROMPT as PLAN_SYSTEM_PROMPT
from agent_orchestrator.graph.plan_nodes import (
    _conversation_block as _conversation_block,
)
from agent_orchestrator.graph.plan_nodes import _criteria_block as _criteria_block
from agent_orchestrator.graph.plan_nodes import (
    _instructions_block as _instructions_block,
)
from agent_orchestrator.graph.plan_nodes import _skill_block as _skill_block
from agent_orchestrator.graph.plan_nodes import _stub_plan as _stub_plan
from agent_orchestrator.graph.plan_nodes import _text_reply as _text_reply
from agent_orchestrator.graph.plan_nodes import make_plan_node as make_plan_node
from agent_orchestrator.graph.ports import (
    ApprovalDecision,
    LLMPort,
    ToolExtrasFactory,
    ToolkitFactory,
    ToolkitPort,
    ToolReplyPort,
)
from agent_orchestrator.graph.runtime import GraphRuntime, runtime_from_config
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.graph.tool_results import is_tool_error

MAX_TOOL_ROUNDS = 5  # execute 内 function-calling 轮次上限(读→查→截图→收束)
MAX_CONFIRM_CYCLES = 3  # 写操作人工确认轮次上限(防确认循环)


EXECUTE_SYSTEM_PROMPT = (
    "你是任务执行助手。依据目标与计划执行,输出本轮执行结果小结:"
    "2-6 行要点,直接给结果,不要寒暄。"
)


def _scoped_toolkit(
    toolkit: ToolkitPort | None, allowed_tools: list[str] | None
) -> ToolkitPort | None:
    """按 Skill 的 tools 白名单裁剪(与 execute 的口径一致)。

    缺省(None)=不限制;空列表=技能声明不用任何工具(与 execute 的
    ``allowed is not None`` 口径对齐,否则空白名单技能会多一次无谓审批)。
    """
    if toolkit is None:
        return None
    return toolkit.restricted(allowed_tools) if allowed_tools is not None else toolkit


def _empty_toolkit(runtime: GraphRuntime) -> ToolkitPort:
    """只有外部工具、没有基础工具工厂时的兜底空注册表。

    具体类在 services 层(依赖方向:graph 不 import services),由装配方
    通过 GraphPorts.empty_toolkit 注入;未注入即视为装配缺失。
    """
    factory = runtime.ports.empty_toolkit
    if factory is None:
        raise RuntimeError("外部工具有 specs 但装配方未注入 empty_toolkit 工厂")
    return factory()


async def _load_toolkit(
    tenant_id: str,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None,
    runtime: GraphRuntime,
) -> ToolkitPort | None:
    """基础工具 + 外部工具(MCP)。外部工具加载失败不阻断 run。"""
    toolkit = toolkit_factory(tenant_id) if toolkit_factory else None
    if toolkit is not None:
        # 远端工具箱(RemoteToolkit)惰性加载目录;失败向上抛——工具服务开启
        # 时它是必需依赖,本进程没有本机直读兜底(设计 §16.1 R1)。
        ensure = getattr(toolkit, "ensure_ready", None)
        if ensure is not None:
            await ensure()
    if extras_factory is not None:
        try:
            extras = await extras_factory(tenant_id)
        except Exception:  # noqa: BLE001 - MCP 故障降级为基础工具
            extras = []
        if extras:
            base = toolkit if toolkit is not None else _empty_toolkit(runtime)
            toolkit = base.extended(extras)
    return toolkit


async def _has_dangerous_tools(
    state: RunGraphState,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None,
    runtime: GraphRuntime,
) -> bool:
    """本 run 实际可用的工具里有没有危险操作(决定审批闸要不要中断)。

    口径 = 与该 run 执行阶段相同的完整工具面:基础工具 + 引用的 MCP 工具,
    再按技能 tools 白名单裁剪。工厂缺失(没有工具可调)视为无危险——
    执行阶段也就不可能产生未经确认的对外副作用。
    """
    toolkit = await _load_toolkit(
        state.get("tenant_id", ""), toolkit_factory, extras_factory, runtime
    )
    scoped = _scoped_toolkit(toolkit, state.get("allowed_tools"))
    return scoped is not None and scoped.has_dangerous


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
        answer = interrupt({"plan_markdown": state.get("plan_markdown", "")})
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


def _execute_messages(state: RunGraphState) -> list[dict[str, Any]]:
    done = "\n".join(state.get("results") or []) or "(尚无)"
    return [
        {
            "role": "system",
            "content": EXECUTE_SYSTEM_PROMPT + _skill_block(state),
        },
        {
            "role": "user",
            "content": (
                f"目标:{state.get('goal', '')}\n\n"
                f"计划:\n{state.get('plan_markdown', '')}\n\n"
                f"已完成的结果:\n{done}\n\n请执行下一步并输出结果小结。"
                f"{_instructions_block(state)}"
            ),
        },
    ]


def _assistant_tool_message(reply: ToolReplyPort) -> dict[str, Any]:
    """把模型的工具请求回填进消息历史(OpenAI 嵌套形状)。"""
    return {
        "role": "assistant",
        "content": reply.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in reply.tool_calls
        ],
    }


def _tool_message(call_id: str, output: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": output}


def _record(name: str, arguments: str, output: str) -> dict[str, Any]:
    return {
        "name": name,
        "arguments": arguments,
        "ok": not is_tool_error(output),
        "detail": output[:200],
    }


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def make_execute_node(
    llm: LLMPort | None,
    toolkit_factory: ToolkitFactory | None = None,
    extras_factory: ToolExtrasFactory | None = None,
):
    """execute 节点工厂:LLM 执行;有工具时跑 function-calling 循环(§6.1)。

    - llm=None:确定性桩(dev/测试零外部依赖);
    - 无工具:单轮结果小结;
    - 有工具:≤MAX_TOOL_ROUNDS 轮;轮次用尽再补一次不带工具的收束调用。
      只读与受控写工具(READ/WRITE)直接执行
      并回喂;**危险操作(DANGER)一律不执行**,收集进 pending_writes 交确认闸
      中断等人工放行(§6.2)——放行后由 tool_exec 执行并回喂同一消息流。
    """

    async def execute_node(state: RunGraphState, config=None) -> dict:
        runtime = runtime_from_config(config)
        if llm is None:
            return {
                "results": ["目标已拆解(桩)", "子任务已执行(桩)", "结果已汇总(桩)"],
                "rounds": 1,
            }
        base = _execute_messages(state)
        messages = base + list(state.get("tool_history") or [])
        toolkit = await _load_toolkit(
            state.get("tenant_id", ""), toolkit_factory, extras_factory, runtime
        )
        allowed = state.get("allowed_tools")
        if toolkit is not None and allowed is not None:
            toolkit = toolkit.restricted(allowed)
        if toolkit is None or len(toolkit) == 0:
            text = await _text_reply(
                llm, messages, phase=delta.PHASE_EXECUTE, runtime=runtime
            )
            return {"results": [text], "rounds": 1}

        schemas = toolkit.schemas()
        records: list[dict[str, Any]] = []
        reply: ToolReplyPort | None = None
        exhausted = True
        for _ in range(MAX_TOOL_ROUNDS):
            runtime.ports.usage.check_budget()
            reply = await llm.complete(messages, tools=schemas)
            if not reply.tool_calls:
                exhausted = False
                break
            messages.append(_assistant_tool_message(reply))
            pending: list[dict[str, Any]] = []
            policies = state.get("approval_policies") or []
            for call in reply.tool_calls:
                decision = runtime.ports.approval.decide(
                    policies, call.name, toolkit.risk(call.name)
                )
                if decision is ApprovalDecision.DENY:
                    # 策略禁用:不执行,如实回喂模型(它能据此换个做法)
                    output = _tool_error(
                        f"工具 {call.name} 被租户审批策略禁止使用,本次未执行"
                    )
                    records.append(_record(call.name, call.arguments, output))
                    messages.append(_tool_message(call.id, output))
                    continue
                if decision is ApprovalDecision.CONFIRM:
                    if state.get("confirm_cycles", 0) >= MAX_CONFIRM_CYCLES:
                        # 确认次数达上限:不再打断,如实告知模型未执行
                        output = _tool_error("危险操作确认次数已达上限,本次未执行")
                        records.append(_record(call.name, call.arguments, output))
                        messages.append(_tool_message(call.id, output))
                        continue
                    pending.append(
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                    )
                    continue
                output = await toolkit.execute(call.name, call.arguments)
                records.append(_record(call.name, call.arguments, output))
                messages.append(_tool_message(call.id, output))
            if pending:
                return {
                    "tool_history": messages[len(base) :],
                    "pending_writes": pending,
                    "tool_calls": records,
                }
        if exhausted and records:
            # 轮次用尽仍在要工具:再给一次"不带工具"的收束调用,把已拿到的
            # 工具结果收敛成正文;否则下游只能拿到空正文,误报成"没读到内容"。
            content = await _text_reply(
                llm, messages, phase=delta.PHASE_EXECUTE, runtime=runtime
            )
        else:
            content = reply.content if reply is not None else ""
        text = content or f"(调用工具 {len(records)} 次后模型未给出结论)"
        return {
            "results": [text],
            "rounds": 1,
            "tool_calls": records,
            "tool_history": messages[len(base) :],
            "pending_writes": [],
        }

    return execute_node


def confirm_gate_node(state: RunGraphState) -> dict:
    """危险操作确认闸(§6.2):execute 收集到危险操作后中断,等人工放行/拒绝。

    注意:interrupt 之前不得有任何副作用——LangGraph 恢复时会重放本节点,
    副作用逻辑必须留在 tool_exec 里。
    """
    pending = state.get("pending_writes") or []
    answer = interrupt({"pending_writes": pending})
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


def make_tool_exec_node(
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None = None,
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
        for call in pending:
            name = str(call.get("name") or "")
            arguments = str(call.get("arguments") or "{}")
            if toolkit is None:
                output = _tool_error("工具未配置,写操作未执行")
            else:
                output = await toolkit.execute(name, arguments)
            records.append(_record(name, arguments, output))
            messages.append(_tool_message(str(call.get("id") or ""), output))
        return {
            "tool_history": messages[len(base) :],
            "pending_writes": [],
            "writes_approved": False,
            "tool_calls": records,
            "confirm_cycles": 1,
        }

    return tool_exec_node
