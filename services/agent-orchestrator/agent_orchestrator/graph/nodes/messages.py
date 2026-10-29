"""执行图的消息拼装:execute 系统提示、工具调用/结果消息与清单回写增量。

自 graph/nodes.py 拆出,行为与提示文本不变。
"""

from __future__ import annotations

import json
from typing import Any

from agent_orchestrator.graph.plan_nodes import (
    _context_block,
    _instructions_block,
    _todo_block,
)
from agent_orchestrator.graph.ports import ToolReplyPort
from agent_orchestrator.graph.state import RunGraphState
from agent_orchestrator.graph.todos import TODO_TOOL_NAME, sanitize_todos

EXECUTE_SYSTEM_PROMPT = (
    "你是任务执行助手。依据目标与计划执行,输出本轮执行结果小结:"
    "2-6 行要点,直接给结果,不要寒暄。"
)


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
