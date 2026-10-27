"""执行图 plan 节点与提示词(自 nodes.py 拆出)。"""

from __future__ import annotations

from typing import Any

from agent_orchestrator.graph import delta
from agent_orchestrator.graph.ports import (
    LLMPort,
)
from agent_orchestrator.graph.runtime import GraphRuntime, runtime_from_config
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.graph.todos import render_todo_block

PLAN_SYSTEM_PROMPT = (
    "你是任务规划助手。依据目标与验收标准产出 Markdown 执行计划:"
    "以「# 执行计划」开头,含「## 步骤」编号列表,每步是一个可执行动作;"
    "末尾「## 验收标准」用「- [ ] 」清单逐条列出。不要输出计划之外的任何文字。"
)


async def _text_reply(
    llm: LLMPort,
    messages: list[dict[str, Any]],
    *,
    phase: str,
    runtime: GraphRuntime,
) -> str:
    """取一段模型正文:有增量通道时边流边报(§6.3 token.delta.v1)。

    节点仍然只上报文本、不碰数据库——落库在 services 层的图调用边界。
    注入实现(测试假客户端等)不一定支持 stream,没有就退回 complete,
    行为与不流式时完全一致。调用前先过 token 预算闸(P0-3)。
    """
    runtime.ports.usage.check_budget()
    if not runtime.delta.active() or not hasattr(llm, "stream"):
        return (await llm.complete(messages)).content
    parts: list[str] = []
    async for piece in llm.stream(messages):
        parts.append(piece)
        await runtime.delta.emit_text(piece, phase)
    return "".join(parts)


def _skill_block(state: RunGraphState) -> str:
    """Skill 约束(系统提示词):plan/execute/verify/deliver 都要遵循。"""
    prompt = state.get("skill_system_prompt")
    if not prompt:
        return ""
    return f"\n\n技能约束(必须遵循):\n{prompt}"


def _memory_block(state: RunGraphState) -> str:
    """长期记忆召回块(§6.7):跨会话沉淀的偏好/事实,四个节点都要参考。

    内容由 services 层在起图时按目标检索拼好(自带小标题),空串=没召回;
    节点是纯函数只读它,召回失败在服务层降级为空,不阻断执行。
    """
    block = state.get("memory_block")
    if not block:
        return ""
    return f"\n\n{block}"


def _context_block(state: RunGraphState) -> str:
    """上下文工程注入块(P1-F,§7.3):统一 plan/execute/verify/deliver 装配。

    有 context_block(services 层用 ContextPipeline 渲染好的 AGENTS.md /
    技能卡片 / 记忆段)时用它;没有(旧 checkpoint、未装配)时回退改造前的
    「技能约束 + 记忆召回」两段,行为与旧版严格一致。返回值可直接拼进系统
    提示词(自带前置空行),空串=两段都没有。
    """
    block = state.get("context_block")
    if block:
        return f"\n\n{block}"
    return _skill_block(state) + _memory_block(state)


def _todo_block(state: RunGraphState) -> str:
    """任务清单块(M4):每轮消息重建时注入最新清单,空清单返回空串。

    替换式而不是累积式:历史 tool 消息里已经有一份清单快照,再往系统提示里
    叠就等于同一份清单占两处上下文;这里始终只用 state 里的最新一份。
    """

    block = render_todo_block(state.get("todos"))
    if not block:
        return ""
    return f"\n\n{block}"


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


def make_plan_node(llm: LLMPort | None):
    """plan 节点工厂:LLM 生成计划(§6.1 可编辑工件)。

    resume 重试时保留用户已编辑的计划,不覆盖(先判 plan_markdown)。
    """

    async def plan_node(state: RunGraphState, config=None) -> dict:
        # config 由 LangGraph 运行时注入(带本次调用的 GraphRuntime);
        # 直跑节点的单测可只传 state,此时回退默认运行期。
        runtime = runtime_from_config(config)
        if state.get("plan_markdown"):
            return {}
        if llm is None:
            return _stub_plan(state)
        criteria = state.get("acceptance_criteria") or []
        plan_markdown = await _text_reply(
            llm,
            [
                {
                    "role": "system",
                    "content": PLAN_SYSTEM_PROMPT + _context_block(state),
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
            ],
            phase=delta.PHASE_PLAN,
            runtime=runtime,
        )
        return {"plan_markdown": plan_markdown}

    return plan_node
