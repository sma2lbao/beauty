"""对话多轮上下文:装载口径与截断(任务规划与直接对话共用,§6.7)。

两条路径都需要"把之前说过的话带给模型",但装载口径不同:

- 任务(runs):排除本 run 自己的消息——触发消息是 goal、追加指令走指令队列,
  见 MessageRepository.list_history;
- 直接对话(chat):带此前所有轮次(不分 run),见 MessageRepository.list_before。

截断口径一致:最近 HISTORY_MESSAGES_LIMIT 条、单条 HISTORY_MESSAGE_CHARS 字
——交付物可能很长,不能整篇塞回提示词。
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_orchestrator.models.conversations import ChatMessage

#: 多轮上下文带回的近期消息上限与单条截断长度(交付物可能很长)
HISTORY_MESSAGES_LIMIT = 20
HISTORY_MESSAGE_CHARS = 2000


def to_prompt(rows: Iterable[ChatMessage]) -> list[dict[str, str]]:
    """ORM 消息 → 提示词用的 {role, content}(取最近若干条,单条截断)。"""
    items = list(rows)
    return [
        {"role": row.role, "content": (row.content or "")[:HISTORY_MESSAGE_CHARS]}
        for row in items[-HISTORY_MESSAGES_LIMIT:]
    ]
