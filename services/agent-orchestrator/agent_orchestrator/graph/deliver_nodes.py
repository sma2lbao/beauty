"""执行图 verify/deliver 节点与提示词(自 nodes.py 拆出)。"""

from __future__ import annotations

import json
import re
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
    "你是交付助手。依据目标、计划与执行结果,给出面向用户的最终结果:"
    "直接给成品,不要解释过程、不要寒暄、不要重复计划本身。"
    "默认结果会作为会话回复直接展示:用 Markdown 组织清楚即可,"
    '但不要声称已生成/保存了文件,也不要写"见附件";'
    "只有用户明确要文件或文档(Markdown、报告、PDF 等)时,才输出完整成稿。"
    "若执行阶段已把可运行成品写成工作区文件(如 snake.html),该文件会作为独立"
    "交付物与这份说明一起提供:正文只写它是什么、怎么打开/运行、怎么改,"
    "不要把整段源码再贴一遍;没有独立成品文件时才给出完整成品正文。"
)

#: 用户明确要“文件型成果”的说法(交付阶段据此决定是否落 .md 交付物)。
#: 只看目标与追加指令:title 是模型概括,不代表用户诉求。
_FILE_DELIVERABLE_PATTERN = re.compile(
    r"markdown|md\s*(?:文件|文档|格式)|\.md\b|文档|报告|周报|日报|月报|年报|"
    r"说明书|白皮书|方案书|计划书|导出|保存(?:成|为)|存成文件|写成文件|"
    r"生成文件|输出文件|\bpdf\b|\bword\b|\bdocx\b|\bxlsx\b|\bpptx\b",
    re.IGNORECASE,
)


def wants_file_deliverable(state: RunGraphState) -> bool:
    """用户是否明确要可下载的文件型成果(§7)。

    交付默认只把正文作为会话回复展示:没要文件时不落 .md 交付物,避免每个
    任务结束都多出一个 Markdown 文件。判定只用用户侧文本(目标 + 追加指令),
    关键词是启发式——宁可漏判(直接展示)也不错判(多落一个文件)。
    """
    texts = [str(state.get("goal") or "")]
    texts.extend(str(item) for item in state.get("extra_instructions") or [])
    haystack = "\n".join(texts).lower()
    return _FILE_DELIVERABLE_PATTERN.search(haystack) is not None


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


def _delivery_update(state: RunGraphState, content: str) -> dict[str, Any]:
    """交付增量:正文始终进 final_text;只有用户要文件型成果时才登记 artifact。"""
    update: dict[str, Any] = {"final_status": "completed", "final_text": content}
    if wants_file_deliverable(state):
        update["artifact"] = _artifact(state, content)
    return update


def _stub_deliver(state: RunGraphState) -> dict:
    """桩:把目标/执行结果/验收标准拼成交付正文,并标注桩来源。"""
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
    return _delivery_update(state, content)


def make_deliver_node(llm: LLMPort | None):
    """deliver 节点工厂:LLM 合成最终交付正文(§7);llm=None 回退拼装桩。

    正文始终进 final_text(会话直接展示);用户明确要文件时才登记 artifact。
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
        return _delivery_update(state, content)

    return deliver_node
