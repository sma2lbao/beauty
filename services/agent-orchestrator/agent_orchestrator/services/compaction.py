"""短期上下文压缩:把溢出的早期轮次折叠成摘要(§6.7 记忆模块)。

为什么放在服务层而不是执行图节点里:压缩要额外调一次模型,而执行图的一次
invoke 只该发"业务上必要的"模型请求;把压缩放到请求边界(chat/task 发消息、
建 run 之前)可以复用已有的 LLM 客户端,也让执行图保持"节点只读状态"。

折叠口径:

- 摘要落 conversation_summaries(每个对话一条,记录覆盖到哪个时间点),
  **原始消息一条都不删**——审计、回放、重新压缩都依赖原文;
- 只折叠"最近 context_keep_recent_messages 条之外"的部分,近期原文继续
  按 history.to_prompt 的截断口径带入;
- 触发阈值按估算 token 计(中文约 1 字/token,见 estimate_tokens);
- 压缩失败(模型报错/空输出)一律降级为"不压缩",不能因此让一条对话失败。

run 执行时不再调模型压缩:建 run 之前已经压缩并落库,执行图只读摘要
(见 run_lifecycle._conversation_history)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import (
    ConversationSummaryRepository,
    MessageRepository,
)
from agent_orchestrator.services import history

if TYPE_CHECKING:
    from agent_orchestrator.services.llm import LLMClient

logger = get_logger(__name__)

#: 摘要器指令:要求"信息密度优先",保留目标/约束/决定/未完成项,不写客套话。
SUMMARY_SYSTEM_PROMPT = (
    "你是对话摘要器。把给定的对话压缩成一段中文摘要,供后续轮次当上下文使用。"
    "只保留:用户的目标与偏好、已确认的约束/事实、做过的决定及原因、"
    "未完成的事项、以及后续可能复用的结论。不要写寒暄、不要编造未出现的信息、"
    "不要用 Markdown 标题,直接输出一段连续文本。"
)

#: 单次压缩送入模型的原文上限(字符):一次折叠过多会稀释摘要质量,
#: 超出部分留到下次压缩(从最老的开始折叠,保证覆盖点单调前进)。
SUMMARY_MAX_INPUT_CHARS = 12_000


@dataclass(frozen=True)
class CompactionResult:
    """一次压缩的结果(落库后返回给调用方记事件/日志)。"""

    summary: str
    covered_until: datetime
    covered_messages: int
    tokens_before: int
    tokens_after: int


def estimate_tokens(text: str) -> int:
    """粗略 token 估算:按 2 字符/token 取偏保守值(中文约 1 字/token)。

    只用于"要不要压缩"的阈值判断,不要求精确(真实用量由网关回传统计)。
    """
    if not text:
        return 0
    return max(1, len(text) // 2)


def _render(rows: list[ChatMessage]) -> str:
    """把待压缩消息渲染成摘要器的输入(单条仍按 history 上限截断)。"""
    return "\n".join(
        f"{row.role}: {(row.content or '')[: history.HISTORY_MESSAGE_CHARS]}"
        for row in rows
    )


async def summarize(
    llm: LLMClient,
    *,
    previous_summary: str,
    rows: list[ChatMessage],
    max_chars: int,
    max_input_chars: int = SUMMARY_MAX_INPUT_CHARS,
) -> str:
    """把"旧摘要 + 一批消息"合并成新摘要;模型空输出视为失败(抛 ValueError)。"""
    parts: list[str] = []
    if previous_summary.strip():
        parts.append(f"【已有摘要】\n{previous_summary.strip()}")
    parts.append(f"【新增待压缩对话】\n{_render(rows)}")
    prompt = "\n\n".join(parts)[:max_input_chars]
    reply = await llm.complete(
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
    )
    summary = (reply.content or "").strip()
    if not summary:
        raise ValueError("compaction.summarizer_empty")
    return summary[:max_chars]


async def summary_for(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> tuple[str, datetime | None]:
    """取已落库的对话摘要(无则空串/None);chat 与 run 建图共用。"""
    record = await ConversationSummaryRepository(session).get_for_conversation(
        tenant_id, conversation_id
    )
    if record is None:
        return "", None
    return record.summary, record.covered_until


async def compact_history(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    llm: LLMClient | None,
    *,
    rows: list[ChatMessage],
    threshold_tokens: int,
    keep_recent: int,
    summary_max_chars: int,
) -> CompactionResult | None:
    """必要时压缩该对话的早期轮次并落库;不需要(或失败)返回 None。

    rows 是调用方已按"当前消息之前"过滤好的全部历史(未截断)。返回的摘要
    已 upsert 到 conversation_summaries,调用方可直接用 CompactionResult 记事件。
    """
    if llm is None or not rows:
        return None
    existing = await ConversationSummaryRepository(session).get_for_conversation(
        tenant_id, conversation_id
    )
    previous = existing.summary if existing is not None else ""
    covered_until = existing.covered_until if existing is not None else None
    fresh = history.filter_after(rows, covered_until)
    if len(fresh) <= keep_recent:
        return None
    tokens_before = estimate_tokens(previous) + sum(
        estimate_tokens(row.content or "") for row in fresh
    )
    if tokens_before <= threshold_tokens:
        return None
    folded = fresh[:-keep_recent]
    try:
        summary = await summarize(
            llm, previous_summary=previous, rows=folded, max_chars=summary_max_chars
        )
    except Exception as exc:  # noqa: BLE001 - 压缩失败只降级,不阻断对话/建 run
        logger.warning(
            "context compaction failed",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None
    new_covered_until = folded[-1].created_at
    if new_covered_until is None:  # 防御:时间戳缺失时用最后一条的消息时间兜底
        new_covered_until = rows[-1].created_at
    if new_covered_until is None:
        return None
    covered_messages = (existing.covered_messages if existing is not None else 0) + len(
        folded
    )
    tokens_after = estimate_tokens(summary) + sum(
        estimate_tokens(row.content or "") for row in fresh[-keep_recent:]
    )
    await ConversationSummaryRepository(session).upsert(
        tenant_id,
        conversation_id,
        summary=summary,
        covered_until=new_covered_until,
        covered_messages=covered_messages,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )
    return CompactionResult(
        summary=summary,
        covered_until=new_covered_until,
        covered_messages=covered_messages,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )


async def rows_before_current(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    message: ChatMessage,
) -> list[ChatMessage]:
    """取当前消息之前的全部对话消息(chat/task 压缩的共同输入)。"""
    return await MessageRepository(session).list_before(
        tenant_id,
        conversation_id,
        before=message.created_at,
        exclude_id=message.id,
    )


async def compact_conversation(
    session: AsyncSession,
    tenant_id: str,
    conversation_id: str,
    llm: LLMClient | None,
    *,
    message: ChatMessage,
    threshold_tokens: int,
    keep_recent: int,
    summary_max_chars: int,
) -> CompactionResult | None:
    """请求边界用的压缩入口:取"当前消息之前"的全部历史,必要时折叠。

    只压缩、不返回摘要文本给提示词——提示词统一走 summary_for 读落库结果,
    保证"压缩历史"与"渲染提示词"看到同一份摘要。
    """
    rows = await rows_before_current(session, tenant_id, conversation_id, message)
    return await compact_history(
        session,
        tenant_id,
        conversation_id,
        llm,
        rows=rows,
        threshold_tokens=threshold_tokens,
        keep_recent=keep_recent,
        summary_max_chars=summary_max_chars,
    )
