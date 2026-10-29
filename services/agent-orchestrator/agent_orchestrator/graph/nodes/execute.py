"""execute 节点:LLM 执行 + function-calling 循环 + 危险操作挂起(设计 §6.1/§6.2)。

自 graph/nodes.py 拆出,行为与上限常量不变。
"""

from __future__ import annotations

import time
from typing import Any

from agent_orchestrator.graph import delta, items
from agent_orchestrator.graph.nodes.ask import (
    MAX_ASK_CYCLES,
    _build_pending_question,
    _tool_kind,
    _validate_call,
)
from agent_orchestrator.graph.nodes.audit import (
    DECISION_AUTO,
    DECISION_DENIED,
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
    _assistant_tool_message,
    _execute_messages,
    _todos_state,
    _todos_update,
    _tool_message,
)
from agent_orchestrator.graph.nodes.toolkit import _load_toolkit
from agent_orchestrator.graph.plan_nodes import _text_reply
from agent_orchestrator.graph.ports import (
    KIND_INTERACTION,
    ApprovalDecision,
    LLMPort,
    OffloadPort,
    ToolExtrasFactory,
    ToolkitFactory,
    ToolReplyPort,
)
from agent_orchestrator.graph.runtime import runtime_from_config
from agent_orchestrator.graph.state import RunGraphState

MAX_TOOL_ROUNDS = 5  # execute 内 function-calling 轮次上限(读→查→截图→收束)
MAX_CONFIRM_CYCLES = 3  # 写操作人工确认轮次上限(防确认循环)


def make_execute_node(
    llm: LLMPort | None,
    toolkit_factory: ToolkitFactory | None = None,
    extras_factory: ToolExtrasFactory | None = None,
    *,
    question_timeout_seconds: int = 900,
    offloader: OffloadPort | None = None,
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
        if toolkit is not None:
            # 执行图可见 interaction 工具(chat 路径不打开,§10.9)
            with_interaction = getattr(toolkit, "with_interaction", None)
            if callable(with_interaction):
                toolkit = with_interaction()
        if toolkit is None or len(toolkit) == 0:
            text = await _text_reply(
                llm, messages, phase=delta.PHASE_EXECUTE, runtime=runtime
            )
            return {"results": [text], "rounds": 1}

        schemas = toolkit.schemas()
        records: list[dict[str, Any]] = []
        reply: ToolReplyPort | None = None
        latest_todos: list[dict[str, str]] | None = None
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
            skip_ids: set[str] = set()
            ask_call = None
            ask_reason = ""
            for call in reply.tool_calls or []:
                if _tool_kind(toolkit, call.name) != KIND_INTERACTION:
                    continue
                risk = str(toolkit.risk(call.name))
                if (
                    runtime.ports.approval.decide(policies, call.name, risk)
                    is ApprovalDecision.DENY
                ):
                    # 租户策略禁用提问:走普通工具的"策略禁止"分支回喂
                    continue
                if state.get("ask_cycles", 0) >= MAX_ASK_CYCLES:
                    ask_reason = f"提问次数已达上限({MAX_ASK_CYCLES}),本次未向用户提问"
                    ask_call = call
                    break
                errors = _validate_call(toolkit, call.name, call.arguments)
                if errors:
                    ask_reason = "提问参数不合法:" + ";".join(errors)
                    ask_call = call
                    break
                ask_call = call
                break
            if ask_call is not None and not ask_reason:
                # 命中交互调用:本回合只处理提问,其余调用一律不执行——
                # ask_gate 中断前零副作用,恢复后由模型下一轮基于答案继续。
                question = _build_pending_question(ask_call, question_timeout_seconds)
                for other in reply.tool_calls or []:
                    if other is ask_call:
                        continue
                    output = _tool_error("本轮包含需要用户回答的提问,其余工具未执行")
                    record = _record(
                        other.name,
                        other.arguments,
                        output,
                        tool_call_id=getattr(other, "id", None),
                        risk=str(toolkit.risk(other.name)),
                        decision=DECISION_DENIED,
                    )
                    records.append(record)
                    _tool_item_denied(
                        runtime, other.name, other.arguments, record, node="execute"
                    )
                    messages.append(_tool_message(other.id, output))
                record = _record(
                    ask_call.name,
                    ask_call.arguments,
                    _tool_error("已向用户发起提问,等待回答"),
                    tool_call_id=getattr(ask_call, "id", None),
                    risk=str(toolkit.risk(ask_call.name)),
                    decision=DECISION_AUTO,
                )
                records.append(record)
                _tool_item_denied(
                    runtime, ask_call.name, ask_call.arguments, record, node="execute"
                )
                return {
                    "tool_history": messages[len(base) :],
                    "pending_question": question,
                    "tool_calls": records,
                    **_todos_state(latest_todos),
                }
            if ask_call is not None:
                # 不能挂起(轮次用尽/参数非法):如实回喂,本轮其余工具照常执行
                output = _tool_error(ask_reason)
                record = _record(
                    ask_call.name,
                    ask_call.arguments,
                    output,
                    tool_call_id=getattr(ask_call, "id", None),
                    risk=str(toolkit.risk(ask_call.name)),
                    decision=DECISION_DENIED,
                )
                records.append(record)
                _tool_item_denied(
                    runtime, ask_call.name, ask_call.arguments, record, node="execute"
                )
                messages.append(_tool_message(ask_call.id, output))
                skip_ids.add(str(getattr(ask_call, "id", "")))
            for call in reply.tool_calls:
                if str(getattr(call, "id", "")) in skip_ids:
                    continue
                risk = str(toolkit.risk(call.name))
                decision = runtime.ports.approval.decide(policies, call.name, risk)
                if decision is ApprovalDecision.DENY:
                    # 策略禁用:不执行,如实回喂模型(它能据此换个做法)
                    output = _tool_error(
                        f"工具 {call.name} 被租户审批策略禁止使用,本次未执行"
                    )
                    record = _record(
                        call.name,
                        call.arguments,
                        output,
                        tool_call_id=getattr(call, "id", None),
                        risk=risk,
                        decision=DECISION_DENIED,
                    )
                    records.append(record)
                    _tool_item_denied(
                        runtime, call.name, call.arguments, record, node="execute"
                    )
                    messages.append(_tool_message(call.id, output))
                    continue
                if decision is ApprovalDecision.CONFIRM:
                    if state.get("confirm_cycles", 0) >= MAX_CONFIRM_CYCLES:
                        # 确认次数达上限:不再打断,如实告知模型未执行
                        output = _tool_error("危险操作确认次数已达上限,本次未执行")
                        record = _record(
                            call.name,
                            call.arguments,
                            output,
                            tool_call_id=getattr(call, "id", None),
                            risk=risk,
                            decision=DECISION_DENIED,
                        )
                        records.append(record)
                        _tool_item_denied(
                            runtime, call.name, call.arguments, record, node="execute"
                        )
                        messages.append(_tool_message(call.id, output))
                        continue
                    pending.append(
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                    )
                    continue
                # 策略 auto:审批流程已放行(不等于人工确认),照实执行并透传
                # approved 作为服务端写操作的风险下限凭据。
                item_kind = items.classify_tool_kind(call.name)
                item_span: items.ItemSpan | None = None
                if call.name not in items.REPORTED_BY_SERVICE_TOOLS:
                    # todo_write 的清单快照由 tools.py 落成 kind=todo item、
                    # spawn_subagent 的子代理项由 services.subagents 落成
                    # kind=subagent item,这里不再重复报一条同名工具项
                    # (denied 分支仍如实上报)。
                    item_kind, item_span = _tool_item_begin(
                        runtime, call.name, call.arguments, node="execute"
                    )
                started = time.perf_counter()
                try:
                    output = await toolkit.execute(
                        call.name,
                        call.arguments,
                        tool_call_id=getattr(call, "id", None),
                        approved=True,
                    )
                except Exception as exc:
                    _tool_item_fail(runtime, item_span, call.name, call.arguments, exc)
                    raise
                update = _todos_update(call.name, output)
                if update is not None:
                    latest_todos = update
                # 大结果 offload:回喂模型的 tool 消息与审计都记信封,
                # result_chars 因此是信封长度(原始长度在 record 里)。
                output, offload_record = await _maybe_offload(
                    offloader, call.name, output
                )
                record = _record(
                    call.name,
                    call.arguments,
                    output,
                    tool_call_id=getattr(call, "id", None),
                    risk=risk,
                    decision=DECISION_AUTO,
                    duration_ms=_elapsed_ms(started),
                    offload=offload_record,
                )
                records.append(record)
                _tool_item_finish(
                    runtime, item_kind, call.name, call.arguments, item_span, record
                )
                messages.append(_tool_message(call.id, output))
            if pending:
                return {
                    "tool_history": messages[len(base) :],
                    "pending_writes": pending,
                    "tool_calls": records,
                    **_todos_state(latest_todos),
                }
        if exhausted and records:
            # 轮次用尽仍在要工具:再给一次"不带工具"的收束调用,把已拿到的
            # 工具结果收敛成正文;否则下游只能拿到空正文,误报成"没读到内容"。
            content = await _text_reply(
                llm, messages, phase=delta.PHASE_EXECUTE, runtime=runtime
            )
        else:
            content = reply.content if reply is not None else ""
            if content:
                # function-calling 的收口走的是 complete,不是 _text_reply 的
                # 流式调用;这里补报一次最终正文(§6.3),让"有工具"的 run
                # 在 execute 阶段也有 token.delta.v1。只报这一处:轮次用尽
                # 回退 _text_reply 的分支已自行上报,不重复。
                await runtime.delta.emit_text(content, delta.PHASE_EXECUTE)
        text = content or f"(调用工具 {len(records)} 次后模型未给出结论)"
        return {
            "results": [text],
            "rounds": 1,
            "tool_calls": records,
            "tool_history": messages[len(base) :],
            "pending_writes": [],
            **_todos_state(latest_todos),
        }

    return execute_node
