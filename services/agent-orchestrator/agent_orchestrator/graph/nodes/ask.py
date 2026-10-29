"""交互提问节点(设计 §10):execute 收集到待答问题后中断等回答。

自 graph/nodes.py 拆出,question 载荷与固定回喂文案不变。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from langgraph.types import interrupt

from agent_orchestrator.graph.nodes.messages import _tool_message
from agent_orchestrator.graph.ports import KIND_FUNCTION, ToolkitPort
from agent_orchestrator.graph.state import RunGraphState

MAX_ASK_CYCLES = 3  # 交互提问轮次上限(tool-service §10.3,防提问循环)

#: 提问被拒绝/超时后回喂模型的固定文案(§10.3):不抛错、不阻塞,让模型收束。
QUESTION_DECLINED_REPLY = "用户拒绝回答该问题,请基于已有信息继续或结束任务"
QUESTION_TIMEOUT_REPLY = "用户未在时限内回答,请基于已有信息继续"

#: 单个问题/单条答案进 tool_history 前的字符上限(§10.8)。
MAX_QUESTION_CHARS = 1000
MAX_ANSWER_CHARS = 2000


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
