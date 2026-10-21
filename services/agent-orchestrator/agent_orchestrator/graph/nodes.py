"""执行图节点:plan/execute/verify 接 llm-gateway(架构 §5),deliver 为桩。

约定(架构 §4 分层):节点是纯函数——只读 state、返回增量,
不碰数据库、不发事件;事务与事件在 services 层的图调用边界统一处理。

LLM 注入:builder 用 make_*_node(llm) 工厂装配;llm 为 None 时回退
确定性桩(dev/测试零外部依赖)。节点内 LLM 异常不捕获,由 services 层的
图调用边界统一转 failed 终态(§6.2)。

工具注入:execute 拿 toolkit_factory(租户 → 工具注册表);工具执行失败
不抛错,以错误文本回喂模型(§6.1),每次调用记入 state["tool_calls"]。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.services.llm import LLMReply

# ToolRegistry 在 _load_toolkit 里**运行期**构造(base = toolkit or ToolRegistry([])),
# 不能只放在 TYPE_CHECKING 里——只有外部工具(extras)、没有基础工具工厂时
# 会抛 NameError(P3-C 的纯 skill_run run 正是这种形态)。
from agent_orchestrator.services.tools import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.llm import LLMClient
    from agent_orchestrator.services.tools import ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    ToolExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]

MAX_TOOL_ROUNDS = 5  # execute 内 function-calling 轮次上限(读→查→截图→收束)
MAX_CONFIRM_CYCLES = 3  # 写操作人工确认轮次上限(防确认循环)

PLAN_SYSTEM_PROMPT = (
    "你是任务规划助手。依据目标与验收标准产出 Markdown 执行计划:"
    "以「# 执行计划」开头,含「## 步骤」编号列表,每步是一个可执行动作;"
    "末尾「## 验收标准」用「- [ ] 」清单逐条列出。不要输出计划之外的任何文字。"
)

EXECUTE_SYSTEM_PROMPT = (
    "你是任务执行助手。依据目标与计划执行,输出本轮执行结果小结:"
    "2-6 行要点,直接给结果,不要寒暄。"
)

VERIFY_SYSTEM_PROMPT = (
    "你是验收助手。依据目标、计划与执行结果,逐条判定每项验收标准是否通过。"
    '只输出 JSON 对象:{"summary": "一句话总结", '
    '"verdicts": {"<验收标准原文>": true 或 false}},'
    "每项标准都要出现在 verdicts 里,不要输出 JSON 以外的任何文字。"
)

DELIVER_SYSTEM_PROMPT = (
    "你是交付助手。依据目标、计划与执行结果,产出**最终交付物正文**(Markdown):"
    "直接给成品,不要解释过程、不要寒暄、不要重复计划本身。"
)


def _skill_block(state: RunGraphState) -> str:
    """Skill 约束(系统提示词):plan/execute/verify/deliver 都要遵循。"""
    prompt = state.get("skill_system_prompt")
    if not prompt:
        return ""
    return f"\n\n技能约束(必须遵循):\n{prompt}"


def _criteria_block(criteria: list[str]) -> str:
    return "\n".join(f"- {c}" for c in criteria) or "(无显式验收标准)"


def _instructions_block(state: RunGraphState) -> str:
    """用户追加指令(§6.5):消费后并入各节点提示词,模型必须遵循。"""
    instructions = state.get("extra_instructions") or []
    if not instructions:
        return ""
    lines = "\n".join(f"- {item}" for item in instructions)
    return f"\n\n用户追加指令(必须遵循):\n{lines}"


def _conversation_block(state: RunGraphState) -> str:
    """对话多轮上下文:让模型理解"接着刚才的做"(非首条消息时才有)。"""
    history = state.get("conversation_history") or []
    if not history:
        return ""
    role_labels = {"user": "用户", "assistant": "助手"}
    lines = "\n".join(
        f"{role_labels.get(str(item.get('role')), '用户')}: {item.get('content') or ''}"
        for item in history
    )
    return f"\n\n之前的对话(多轮上下文,理解当前目标用):\n{lines}"


def _stub_plan(state: RunGraphState) -> dict:
    criteria = state.get("acceptance_criteria") or []
    checklist = "\n".join(f"- [ ] {c}" for c in criteria) or "- [ ] (无显式验收标准)"
    instructions = state.get("extra_instructions") or []
    extra = ""
    if instructions:
        lines = "\n".join(f"- {item}" for item in instructions)
        extra = f"\n## 用户追加指令\n\n{lines}\n"
    plan = f"""# 执行计划:{state["title"]}

## 目标

{state["goal"]}

## 步骤

1. 解析目标,拆解子任务
2. 执行子任务并记录结果
3. 逐项核对验收标准
4. 产出交付物

## 验收标准

{checklist}
{extra}"""
    return {"plan_markdown": plan}


def make_plan_node(llm: LLMClient | None):
    """plan 节点工厂:LLM 生成计划(§6.1 可编辑工件)。

    resume 重试时保留用户已编辑的计划,不覆盖(先判 plan_markdown)。
    """

    async def plan_node(state: RunGraphState) -> dict:
        if state.get("plan_markdown"):
            return {}
        if llm is None:
            return _stub_plan(state)
        criteria = state.get("acceptance_criteria") or []
        reply = await llm.complete(
            [
                {
                    "role": "system",
                    "content": PLAN_SYSTEM_PROMPT + _skill_block(state),
                },
                {
                    "role": "user",
                    "content": (
                        f"目标:{state.get('goal', '')}\n"
                        f"标题:{state.get('title', '')}\n"
                        f"验收标准:\n{_criteria_block(criteria)}"
                        f"{_conversation_block(state)}"
                        f"{_instructions_block(state)}"
                    ),
                },
            ]
        )
        return {"plan_markdown": reply.content}

    return plan_node


def _scoped_toolkit(
    toolkit: ToolRegistry | None, allowed_tools: list[str] | None
) -> ToolRegistry | None:
    """按 Skill 的 tools 白名单裁剪(与 execute 的口径一致)。

    缺省(None)=不限制;空列表=技能声明不用任何工具(与 execute 的
    ``allowed is not None`` 口径对齐,否则空白名单技能会多一次无谓审批)。
    """
    if toolkit is None:
        return None
    return toolkit.restricted(allowed_tools) if allowed_tools is not None else toolkit


async def _has_dangerous_tools(
    state: RunGraphState,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None,
) -> bool:
    """本 run 实际可用的工具里有没有危险操作(决定审批闸要不要中断)。

    口径 = 与该 run 执行阶段相同的完整工具面:基础工具 + 引用的 MCP 工具,
    再按技能 tools 白名单裁剪。工厂缺失(没有工具可调)视为无危险——
    执行阶段也就不可能产生未经确认的对外副作用。
    """
    toolkit = await _load_toolkit(
        state.get("tenant_id", ""), toolkit_factory, extras_factory
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

    async def approval_gate_node(state: RunGraphState) -> dict:
        if auto_approve and not await _has_dangerous_tools(
            state, toolkit_factory, extras_factory
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


def _assistant_tool_message(reply: LLMReply) -> dict[str, Any]:
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
        "ok": not output.startswith('{"error"'),
        "detail": output[:200],
    }


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


async def _load_toolkit(
    tenant_id: str,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ToolExtrasFactory | None,
) -> ToolRegistry | None:
    """基础工具 + 外部工具(MCP)。外部工具加载失败不阻断 run。"""
    toolkit = toolkit_factory(tenant_id) if toolkit_factory else None
    if extras_factory is not None:
        try:
            extras = await extras_factory(tenant_id)
        except Exception:  # noqa: BLE001 - MCP 故障降级为基础工具
            extras = []
        if extras:
            base = toolkit or ToolRegistry([])
            toolkit = base.extended(extras)
    return toolkit


def make_execute_node(
    llm: LLMClient | None,
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

    async def execute_node(state: RunGraphState) -> dict:
        if llm is None:
            return {
                "results": ["目标已拆解(桩)", "子任务已执行(桩)", "结果已汇总(桩)"],
                "rounds": 1,
            }
        base = _execute_messages(state)
        messages = base + list(state.get("tool_history") or [])
        toolkit = await _load_toolkit(
            state.get("tenant_id", ""), toolkit_factory, extras_factory
        )
        allowed = state.get("allowed_tools")
        if toolkit is not None and allowed is not None:
            toolkit = toolkit.restricted(allowed)
        if toolkit is None or len(toolkit) == 0:
            reply = await llm.complete(messages)
            return {"results": [reply.content], "rounds": 1}

        schemas = toolkit.schemas()
        records: list[dict[str, Any]] = []
        reply = LLMReply(content="")
        exhausted = True
        for _ in range(MAX_TOOL_ROUNDS):
            reply = await llm.complete(messages, tools=schemas)
            if not reply.tool_calls:
                exhausted = False
                break
            messages.append(_assistant_tool_message(reply))
            pending: list[dict[str, Any]] = []
            for call in reply.tool_calls:
                if toolkit.requires_confirmation(call.name):
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
            reply = await llm.complete(messages)
        text = reply.content or f"(调用工具 {len(records)} 次后模型未给出结论)"
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

    async def tool_exec_node(state: RunGraphState) -> dict:
        pending = state.get("pending_writes") or []
        base = _execute_messages(state)
        messages = base + list(state.get("tool_history") or [])
        toolkit = await _load_toolkit(
            state.get("tenant_id", ""), toolkit_factory, extras_factory
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


def _parse_verdicts(text: str, criteria: list[str]) -> tuple[dict[str, bool], str]:
    """解析 LLM 验收 JSON:宽容截取首个 {...};解析失败抛错(边界转 failed)。

    缺失的标准保守判 false——宁可多返工一轮,不谎报通过。
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError(f"验收输出无法解析: {text[:120]}")
    data = json.loads(text[start : end + 1])
    verdicts_raw = data.get("verdicts")
    if not isinstance(verdicts_raw, dict):
        raise RuntimeError(f"验收输出缺 verdicts 对象: {text[:120]}")
    verdicts = {c: bool(verdicts_raw.get(c, False)) for c in criteria}
    return verdicts, str(data.get("summary", ""))


def make_verify_node(llm: LLMClient | None):
    """verify 节点工厂:LLM 逐条判定验收标准(§6.1);llm=None 回退全通过桩。

    验收小结追加进 results——下一轮 execute 带着未过项继续返工。
    """

    async def verify_node(state: RunGraphState) -> dict:
        criteria = state.get("acceptance_criteria") or []
        if llm is None or not criteria:
            return {"verified": {c: True for c in criteria}}
        done = "\n".join(state.get("results") or []) or "(尚无)"
        reply = await llm.complete(
            [
                {
                    "role": "system",
                    "content": VERIFY_SYSTEM_PROMPT + _skill_block(state),
                },
                {
                    "role": "user",
                    "content": (
                        f"目标:{state.get('goal', '')}\n\n"
                        f"计划:\n{state.get('plan_markdown', '')}\n\n"
                        f"执行结果:\n{done}\n\n"
                        f"验收标准:\n{_criteria_block(criteria)}"
                        f"{_instructions_block(state)}"
                    ),
                },
            ]
        )
        verdicts, summary = _parse_verdicts(reply.content, criteria)
        update: dict = {"verified": verdicts}
        if summary:
            update["results"] = [f"验收小结:{summary}"]
        return update

    return verify_node


def fail_node(state: RunGraphState) -> dict:
    """返工耗尽仍未验收:诚实终结为 failed(§6.1/§6.2),不伪装 completed。"""
    verified = state.get("verified") or {}
    failed_items = [c for c, ok in verified.items() if not ok]
    reason = f"验收未通过(已返工 {state.get('rounds', 0)} 轮): " + "; ".join(
        failed_items
    )
    return {"final_status": "failed", "status_reason": reason}


def _artifact(state: RunGraphState, content: str) -> dict[str, Any]:
    return {
        "title": state.get("title") or "交付物",
        "kind": "markdown",
        "content": content,
    }


def _stub_deliver(state: RunGraphState) -> dict:
    """桩:把目标/执行结果/验收标准拼成交付物,并标注桩来源。"""
    results = "\n".join(f"- {r}" for r in state.get("results") or []) or "- (无)"
    criteria = state.get("acceptance_criteria") or []
    checklist = "\n".join(f"- [x] {c}" for c in criteria) or "- (无显式验收标准)"
    content = (
        f"# {state.get('title', '交付物')}\n\n"
        f"## 目标\n\n{state.get('goal', '')}\n\n"
        f"## 执行结果\n\n{results}\n\n"
        f"## 验收标准\n\n{checklist}\n\n"
        "---\n\n(阶段 A 桩:交付物由 LLM 合成,见 LLM_ENABLED=true)\n"
    )
    return {"final_status": "completed", "artifact": _artifact(state, content)}


def make_deliver_node(llm: LLMClient | None):
    """deliver 节点工厂:LLM 合成最终交付物(§7);llm=None 回退拼装桩。

    交付物只进 state(nodes 是纯函数);落库 + run.delivered.v1 在 services 层。
    """

    async def deliver_node(state: RunGraphState) -> dict:
        if llm is None:
            return _stub_deliver(state)
        results = "\n\n".join(state.get("results") or []) or "(无执行结果)"
        reply = await llm.complete(
            [
                {
                    "role": "system",
                    "content": DELIVER_SYSTEM_PROMPT + _skill_block(state),
                },
                {
                    "role": "user",
                    "content": (
                        f"目标:{state.get('goal', '')}\n\n"
                        f"计划:\n{state.get('plan_markdown', '')}\n\n"
                        f"执行结果:\n{results}\n\n"
                        f"验收标准:\n"
                        f"{_criteria_block(state.get('acceptance_criteria') or [])}"
                        f"{_instructions_block(state)}"
                    ),
                },
            ]
        )
        return {
            "final_status": "completed",
            "artifact": _artifact(state, reply.content),
        }

    return deliver_node
