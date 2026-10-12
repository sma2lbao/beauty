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

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_orchestrator.services.llm import LLMClient
    from agent_orchestrator.services.tools import ToolRegistry

    ToolkitFactory = Callable[[str], ToolRegistry]

MAX_TOOL_ROUNDS = 3  # execute 内 function-calling 轮次上限

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


def _criteria_block(criteria: list[str]) -> str:
    return "\n".join(f"- {c}" for c in criteria) or "(无显式验收标准)"


def _stub_plan(state: RunGraphState) -> dict:
    criteria = state.get("acceptance_criteria") or []
    checklist = "\n".join(f"- [ ] {c}" for c in criteria) or "- [ ] (无显式验收标准)"
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
"""
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
                {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"目标:{state.get('goal', '')}\n"
                        f"标题:{state.get('title', '')}\n"
                        f"验收标准:\n{_criteria_block(criteria)}"
                    ),
                },
            ]
        )
        return {"plan_markdown": reply.content}

    return plan_node


def approval_gate_node(state: RunGraphState) -> dict:
    """审批闸门:plan 之后中断,等待 approve/reject(§6.2 人工确认)。"""
    answer = interrupt({"plan_markdown": state.get("plan_markdown", "")})
    if answer.get("action") != "approve":
        return {
            "final_status": "blocked",
            "blocked_reason": answer.get("reason") or "用户拒绝",
        }
    return {}


def _execute_messages(state: RunGraphState) -> list[dict[str, Any]]:
    done = "\n".join(state.get("results") or []) or "(尚无)"
    return [
        {"role": "system", "content": EXECUTE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"目标:{state.get('goal', '')}\n\n"
                f"计划:\n{state.get('plan_markdown', '')}\n\n"
                f"已完成的结果:\n{done}\n\n请执行下一步并输出结果小结。"
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


def make_execute_node(
    llm: LLMClient | None, toolkit_factory: ToolkitFactory | None = None
):
    """execute 节点工厂:LLM 执行;有工具时跑 function-calling 循环(§6.1)。

    - llm=None:确定性桩(dev/测试零外部依赖);
    - 无工具:单轮结果小结(阶段 A 行为不变);
    - 有工具:≤MAX_TOOL_ROUNDS 轮——模型请求工具 → 注册表执行 → 结果回喂,
      工具失败以错误文本回喂让模型决策;每轮记 tool_calls 供 service 层落事件。
    """

    async def execute_node(state: RunGraphState) -> dict:
        if llm is None:
            return {
                "results": ["目标已拆解(桩)", "子任务已执行(桩)", "结果已汇总(桩)"],
                "rounds": 1,
            }
        messages = _execute_messages(state)
        toolkit = (
            toolkit_factory(state.get("tenant_id", "")) if toolkit_factory else None
        )
        if toolkit is None or len(toolkit) == 0:
            reply = await llm.complete(messages)
            return {"results": [reply.content], "rounds": 1}

        schemas = toolkit.schemas()
        records: list[dict[str, Any]] = []
        reply = LLMReply(content="")
        for _ in range(MAX_TOOL_ROUNDS):
            reply = await llm.complete(messages, tools=schemas)
            if not reply.tool_calls:
                break
            messages.append(_assistant_tool_message(reply))
            for call in reply.tool_calls:
                output = await toolkit.execute(call.name, call.arguments)
                records.append(
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "ok": not output.startswith('{"error"'),
                        "detail": output[:200],
                    }
                )
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": output}
                )
        text = reply.content or f"(调用工具 {len(records)} 次后模型未给出结论)"
        return {"results": [text], "rounds": 1, "tool_calls": records}

    return execute_node


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
                {"role": "system", "content": VERIFY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"目标:{state.get('goal', '')}\n\n"
                        f"计划:\n{state.get('plan_markdown', '')}\n\n"
                        f"执行结果:\n{done}\n\n"
                        f"验收标准:\n{_criteria_block(criteria)}"
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
    return {"final_status": "failed", "blocked_reason": reason}


def deliver_node(state: RunGraphState) -> dict:
    """交付。桩:状态置 completed(交付物入对象存储为阶段 B)。"""
    return {"final_status": "completed"}
