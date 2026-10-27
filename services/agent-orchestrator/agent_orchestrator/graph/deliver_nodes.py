"""执行图 verify/deliver 节点与提示词(自 nodes.py 拆出)。"""

from __future__ import annotations

import json
from typing import Any

from agent_orchestrator.graph import delta
from agent_orchestrator.graph.plan_nodes import (
    _context_block,
    _criteria_block,
    _instructions_block,
    _text_reply,
    _todo_block,
)
from agent_orchestrator.graph.ports import (
    LLMPort,
)
from agent_orchestrator.graph.runtime import runtime_from_config
from agent_orchestrator.graph.state import RunGraphState

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


def make_verify_node(llm: LLMPort | None):
    """verify 节点工厂:LLM 逐条判定验收标准(§6.1);llm=None 回退全通过桩。

    验收小结追加进 results——下一轮 execute 带着未过项继续返工。
    """

    async def verify_node(state: RunGraphState, config=None) -> dict:
        runtime = runtime_from_config(config)
        criteria = state.get("acceptance_criteria") or []
        if llm is None or not criteria:
            return {"verified": {c: True for c in criteria}}
        done = "\n".join(state.get("results") or []) or "(尚无)"
        verdict_text = await _text_reply(
            llm,
            [
                {
                    "role": "system",
                    "content": (
                        VERIFY_SYSTEM_PROMPT
                        + _context_block(state)
                        + _todo_block(state)
                    ),
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
            ],
            phase=delta.PHASE_VERIFY,
            runtime=runtime,
        )
        verdicts, summary = _parse_verdicts(verdict_text, criteria)
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


def make_deliver_node(llm: LLMPort | None):
    """deliver 节点工厂:LLM 合成最终交付物(§7);llm=None 回退拼装桩。

    交付物只进 state(nodes 是纯函数);落库 + run.delivered.v1 在 services 层。
    """

    async def deliver_node(state: RunGraphState, config=None) -> dict:
        runtime = runtime_from_config(config)
        if llm is None:
            return _stub_deliver(state)
        results = "\n\n".join(state.get("results") or []) or "(无执行结果)"
        content = await _text_reply(
            llm,
            [
                {
                    "role": "system",
                    "content": (
                        DELIVER_SYSTEM_PROMPT
                        + _context_block(state)
                        + _todo_block(state)
                    ),
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
            ],
            phase=delta.PHASE_DELIVER,
            runtime=runtime,
        )
        return {
            "final_status": "completed",
            "artifact": _artifact(state, content),
        }

    return deliver_node
