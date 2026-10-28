"""对话多轮上下文:装载口径、压缩摘要与截断(任务规划与直接对话共用,§6.7)。

两条路径都需要"把之前说过的话带给模型",但装载口径不同:

- 任务(runs):排除本 run 自己的消息——触发消息是 goal、追加指令走指令队列,
  见 MessageRepository.list_history;
- 直接对话(chat):带此前所有轮次(不分 run),见 MessageRepository.list_before。

截断口径一致:最近 HISTORY_MESSAGES_LIMIT 条、单条 HISTORY_MESSAGE_CHARS 字
——交付物可能很长,不能整篇塞回提示词。

压缩摘要(§6.7 短期记忆)是"截断之外的另一条路":溢出的早期轮次由
services.compaction 折叠成摘要(落 conversation_summaries,原文不删除),
这里只负责把摘要块拼回消息序列——摘要是 system 角色,与用户/助手消息区分。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from agent_orchestrator.models.conversations import ChatMessage

#: 多轮上下文带回的近期消息上限与单条截断长度(交付物可能很长)
HISTORY_MESSAGES_LIMIT = 20
HISTORY_MESSAGE_CHARS = 2000

#: 摘要块的提示词前缀(模型据此知道"更早的原文已折叠")
SUMMARY_PREFIX = "此前对话摘要(更早的原始消息已压缩,供理解上下文):"


def recent(
    rows: Iterable[ChatMessage], *, limit: int = HISTORY_MESSAGES_LIMIT
) -> list[ChatMessage]:
    """取最近若干条(压缩摘要覆盖之前的消息不该再重复带入)。"""
    return list(rows)[-limit:]


def filter_after(
    rows: Sequence[ChatMessage], covered_until: datetime | None
) -> list[ChatMessage]:
    """只保留摘要覆盖时间点之后的消息(摘要已包含更早内容,不再重复)。"""
    if covered_until is None:
        return list(rows)
    return [
        row
        for row in rows
        if row.created_at is not None and row.created_at > covered_until
    ]


def to_prompt(
    rows: Iterable[ChatMessage],
    *,
    summary: str = "",
    limit: int = HISTORY_MESSAGES_LIMIT,
    images_by_message: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """ORM 消息 → 提示词用的 {role, content}(取最近若干条,单条截断)。

    summary 非空时,在最前面插入一条 system 摘要块——它代表"更早的轮次",
    与近期的原文消息一起构成完整上下文。

    images_by_message 非空时(P1-G §7),带图的那一轮 content 变成多模态
    parts 列表(text + image_url),与"当前消息"同一形状;没图仍是字符串,
    旧模型/旧调用方零影响。
    """
    messages: list[dict[str, Any]] = []
    for row in recent(rows, limit=limit):
        text = (row.content or "")[:HISTORY_MESSAGE_CHARS]
        images = (images_by_message or {}).get(row.id) or []
        content: Any = [{"type": "text", "text": text}, *images] if images else text
        messages.append({"role": row.role, "content": content})
    if summary.strip():
        messages.insert(
            0,
            {"role": "system", "content": f"{SUMMARY_PREFIX}\n{summary.strip()}"},
        )
    return messages
