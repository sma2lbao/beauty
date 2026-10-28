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
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from langgraph.types import interrupt

from agent_orchestrator.graph import delta, items
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
    _context_block as _context_block,
)
from agent_orchestrator.graph.plan_nodes import (
    _conversation_block as _conversation_block,
)
from agent_orchestrator.graph.plan_nodes import _criteria_block as _criteria_block
from agent_orchestrator.graph.plan_nodes import (
    _instructions_block as _instructions_block,
)
from agent_orchestrator.graph.plan_nodes import _memory_block as _memory_block
from agent_orchestrator.graph.plan_nodes import _skill_block as _skill_block
from agent_orchestrator.graph.plan_nodes import _stub_plan as _stub_plan
from agent_orchestrator.graph.plan_nodes import _text_reply as _text_reply
from agent_orchestrator.graph.plan_nodes import _todo_block as _todo_block
from agent_orchestrator.graph.plan_nodes import make_plan_node as make_plan_node
from agent_orchestrator.graph.ports import (
    KIND_FUNCTION,
    KIND_INTERACTION,
    RISK_DANGER,
    ApprovalDecision,
    LLMPort,
    OffloadPort,
    ToolExtrasFactory,
    ToolkitFactory,
    ToolkitPort,
    ToolReplyPort,
)
from agent_orchestrator.graph.runtime import GraphRuntime, runtime_from_config
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.graph.todos import TODO_TOOL_NAME, sanitize_todos
from agent_orchestrator.graph.tool_results import (
    is_tool_error,
    parse_tool_error,
    tool_result_fields,
)

MAX_TOOL_ROUNDS = 5  # execute 内 function-calling 轮次上限(读→查→截图→收束)
MAX_CONFIRM_CYCLES = 3  # 写操作人工确认轮次上限(防确认循环)
MAX_ASK_CYCLES = 3  # 交互提问轮次上限(tool-service §10.3,防提问循环)

#: 提问被拒绝/超时后回喂模型的固定文案(§10.3):不抛错、不阻塞,让模型收束。
QUESTION_DECLINED_REPLY = "用户拒绝回答该问题,请基于已有信息继续或结束任务"
QUESTION_TIMEOUT_REPLY = "用户未在时限内回答,请基于已有信息继续"

#: 单个问题/单条答案进 tool_history 前的字符上限(§10.8)。
MAX_QUESTION_CHARS = 1000
MAX_ANSWER_CHARS = 2000


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


def _execute_messages(state: RunGraphState) -> list[dict[str, Any]]:
    done = "\n".join(state.get("results") or []) or "(尚无)"
    return [
        {
            "role": "system",
            "content": (
                EXECUTE_SYSTEM_PROMPT + _context_block(state) + _todo_block(state)
            ),
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


def _todos_update(name: str, output: str) -> list[dict[str, str]] | None:
    """工具结果里带清单(todo_write 成功信封)时取出,供节点回写 state。

    解析失败返回 None:回写是增强,不能因为脏结果打断执行——与"工具失败回喂
    错误文本"同一个降级思路。清单很小,offload 不会命中,但仍按原样防御。
    """

    if name != TODO_TOOL_NAME or not output:
        return None
    try:
        payload = json.loads(output)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "todos" not in payload:
        return None
    return sanitize_todos(payload.get("todos"))


def _todos_state(todos: list[dict[str, str]] | None) -> dict[str, Any]:
    """清单回写增量:没有变化就不带这个键,避免无谓覆盖 state。"""

    return {"todos": todos} if todos is not None else {}


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


#: 审计记录 decision 值域:策略自动放行 / 人工确认后放行 / 未执行(拒绝)。
DECISION_AUTO = "auto"
DECISION_APPROVED = "approved"
DECISION_DENIED = "denied"

#: detail 里保留的结果原文长度(与 P0 一致,前端旧字段语义不变)。
DETAIL_CHARS = 200


def _elapsed_ms(started: float) -> int:
    """perf_counter 起点 -> 毫秒整数(审计不关心微秒)。"""

    return int((time.perf_counter() - started) * 1000)


def _record(
    name: str,
    arguments: str,
    output: str,
    *,
    tool_call_id: str | None = None,
    risk: str = RISK_DANGER,
    decision: str = DECISION_AUTO,
    duration_ms: int = 0,
    offload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """一次工具调用的审计记录(设计 §11.1)。

    P0 字段 name/arguments/ok/detail 语义不变;P1 扩充:

    - tool_call_id:模型的调用 id(tool-service 幂等键,缺省空串);
    - risk:read/write/danger;decision:auto/approved/denied;
    - error_code:从结果信封解析(parse_tool_error),成功为 None;
    - duration_ms:调用方用 perf_counter 计时;result_chars 是原始结果长度;
    - truncated/paths/sha256_before|after:来自成功信封(§7.2/§7.3),
      没有就缺省(False/[]/None),事件 payload 侧再按 16KB 上限裁剪;
    - offload:OffloadPort 的决策记录(offloaded/offload_path/original_chars),
      非空时原样并入(长结果被换成引用信封后,result_chars 是信封长度)。
    """

    fields = tool_result_fields(output)
    record: dict[str, Any] = {
        "name": name,
        "arguments": arguments,
        "ok": not is_tool_error(output),
        "detail": output[:DETAIL_CHARS],
        "tool_call_id": tool_call_id or "",
        "risk": risk,
        "decision": decision,
        "error_code": parse_tool_error(output),
        "duration_ms": duration_ms,
        "result_chars": len(output),
        "truncated": fields.get("truncated", False),
        "paths": fields.get("paths", []),
        "sha256_before": fields.get("sha256_before"),
        "sha256_after": fields.get("sha256_after"),
    }
    if offload:
        # offload 决策自带审计字段;graph 侧只负责并入,不解释其语义。
        record.update(dict(offload))
    return record


async def _maybe_offload(
    offloader: OffloadPort | None,
    tool_name: str,
    output: str,
) -> tuple[str, Mapping[str, Any] | None]:
    """按注入的 offload 口决定是否把大结果换成引用信封(§6.6)。

    未注入 offloader 时原样返回(与改造前完全一致);落盘、配额与失败降级
    都由 OffloadPort 实现负责,graph 侧不感知沙箱细节。
    """
    if offloader is None:
        return output, None
    decision = await offloader.maybe_offload(tool_name, output)
    return decision.content, decision.record


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


# ---------------------------------------------------------------- 过程项(P1-D)


def _tool_item_begin(
    runtime: GraphRuntime, name: str, arguments: str, *, node: str
) -> tuple[str, items.ItemSpan | None]:
    """上报工具项的 ``started`` 相位(§4/§5),返回 (kind, span) 供 finish 续用。

    没有 sink(直跑节点的单测)时 span 为 None,后续 finish/denied 全部 no-op。
    """

    kind = items.classify_tool_kind(name)
    span = runtime.items.begin(
        kind,
        items.tool_item_title(kind, name, arguments),
        node=node,
        data=items.tool_item_data(kind, name, arguments),
    )
    return kind, span


def _tool_item_finish(
    runtime: GraphRuntime,
    kind: str,
    name: str,
    arguments: str,
    span: items.ItemSpan | None,
    record: dict[str, Any],
) -> None:
    """上报 ``completed`` 相位:data 复用节点 _record 产出的审计字段(§4)。"""

    runtime.items.finish(span, data=items.tool_item_data(kind, name, arguments, record))


def _tool_item_denied(
    runtime: GraphRuntime,
    name: str,
    arguments: str,
    record: dict[str, Any],
    *,
    node: str,
) -> None:
    """未执行的调用(策略禁止/确认拒绝/参数非法):只落一条 completed(§5)。

    status=denied,不伪造 started 相位;data 里 decision/ok 如实保留。
    """

    kind = items.classify_tool_kind(name)
    runtime.items.record(
        kind,
        items.tool_item_title(kind, name, arguments),
        status=items.STATUS_DENIED,
        node=node,
        data=items.tool_item_data(kind, name, arguments, record),
    )


def _tool_item_fail(
    runtime: GraphRuntime,
    span: items.ItemSpan | None,
    name: str,
    arguments: str,
    exc: Exception,
) -> None:
    """执行抛异常:把 started 项收成 failed 相位再向上抛(run.failed 由边界补)。"""

    runtime.items.finish(
        span,
        status=items.STATUS_FAILED,
        data={
            "name": name,
            "arguments": arguments,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        },
    )


# ---------------------------------------------------------------- 交互提问(§10)


def _tool_kind(toolkit: ToolkitPort, name: str) -> str:
    """工具形态;旧实现/测试替身没有 kind() 时按普通工具处理(向后兼容)。"""

    hook = getattr(toolkit, "kind", None)
    return str(hook(name)) if callable(hook) else KIND_FUNCTION


def _validate_call(toolkit: ToolkitPort, name: str, arguments: str) -> list[str]:
    """执行前参数预校验;没有校验能力的实现视为通过。"""

    hook = getattr(toolkit, "validate_call", None)
    if not callable(hook):
        return []
    return [str(message) for message in hook(name, arguments)]


def _safe_text(value: Any, limit: int) -> str:
    """去掉控制字符并截断——回答来自外部输入,进消息历史前必须清洗。"""

    text = "".join(
        char for char in str(value) if char in {"\n", "\t"} or char.isprintable()
    )
    return text[:limit]


def format_question_answers(questions: list[dict[str, Any]], answers: Any) -> str:
    """把结构化回答归一成一条 tool-role 消息(§10.3 固定格式)。"""

    by_index: dict[int, dict[str, Any]] = {}
    if isinstance(answers, list):
        for item in answers:
            if isinstance(item, dict) and isinstance(item.get("index"), int):
                by_index[item["index"]] = item
    lines = ["用户已回答你的提问:"]
    for position, question in enumerate(questions):
        prompt = question.get("question", "") if isinstance(question, dict) else ""
        item = by_index.get(position) or {}
        parts = [
            _safe_text(label, MAX_ANSWER_CHARS)
            for label in item.get("labels") or []
            if str(label).strip()
        ]
        custom = item.get("custom")
        if custom and str(custom).strip():
            parts.append(_safe_text(custom, MAX_ANSWER_CHARS))
        value = "、".join(parts) if parts else "(未作答)"
        lines.append(
            f"{position + 1}. {_safe_text(prompt, MAX_QUESTION_CHARS)} -> {value}"
        )
    return "\n".join(lines)


def _build_pending_question(call: Any, timeout_seconds: int) -> dict[str, Any]:
    """由模型给出的工具调用参数构造待答问题(不产生任何副作用)。"""

    try:
        payload = json.loads(call.arguments) if call.arguments else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    raw_questions = payload.get("questions")
    questions = (
        [item for item in raw_questions if isinstance(item, dict)]
        if isinstance(raw_questions, list)
        else []
    )
    allow_custom = payload.get("allow_custom")
    created_at = datetime.now(UTC)
    expires_at = (
        (created_at + timedelta(seconds=timeout_seconds)).isoformat()
        if timeout_seconds > 0
        else None
    )
    return {
        "question_id": uuid4().hex,
        "questions": questions,
        "allow_custom": True if allow_custom is None else bool(allow_custom),
        "created_at": created_at.isoformat(),
        "expires_at": expires_at,
        "tool_call_id": str(getattr(call, "id", "") or ""),
        "tool": getattr(call, "name", ""),
    }


def _question_update(pending: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
    """恢复后把答案归一成 tool-role 消息写回,清空待答状态、计一轮。"""

    history = list(pending.get("_history") or [])
    call_id = str(pending.get("tool_call_id") or "")
    answers: list[dict[str, Any]] = []
    if answer.get("action") == "decline":
        reason = answer.get("reason")
        content = (
            QUESTION_TIMEOUT_REPLY if reason == "timeout" else QUESTION_DECLINED_REPLY
        )
    else:
        raw = answer.get("answers")
        answers = (
            [item for item in raw if isinstance(item, dict)]
            if isinstance(raw, list)
            else []
        )
        content = format_question_answers(
            [item for item in pending.get("questions") or [] if isinstance(item, dict)],
            answers,
        )
    return {
        "tool_history": [*history, _tool_message(call_id, content)],
        "pending_question": None,
        "question_answers": [
            {"question_id": str(pending.get("question_id") or ""), "answers": answers}
        ],
        "ask_cycles": 1,
    }


def make_ask_gate_node():
    """提问闸门(§10.3):execute 已收集到待答问题,这里中断等用户回答。

    这是**唯一新增的 interrupt 点**,且 interrupt 之前零副作用:LangGraph 恢复
    时会重放本节点,任何副作用(写库/发事件/执行工具)都必须留在节点外。
    """

    def ask_gate_node(state: RunGraphState) -> dict:
        pending = state.get("pending_question") or {}
        answer = interrupt(
            {
                "kind": "question",
                "question_id": pending.get("question_id", ""),
                "questions": pending.get("questions", []),
                "allow_custom": pending.get("allow_custom", True),
                "expires_at": pending.get("expires_at"),
            }
        )
        pending = {**pending, "_history": list(state.get("tool_history") or [])}
        return _question_update(pending, answer if isinstance(answer, dict) else {})

    return ask_gate_node


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
