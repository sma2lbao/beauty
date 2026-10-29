"""对话上下文装配:chat 的多轮上下文、压缩与长期记忆召回(自 conversations.py 拆出)。

chat 路径不建 run,上下文要在请求边界自己拼:压缩摘要 + 召回记忆 + 近期原文;
任务路径的压缩在建 run 前做一次(见 ``_task_compaction``),执行图只读已落库摘要。
"""

from __future__ import annotations

from common.logging import get_logger
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.policy import policy_from_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.repositories import MessageRepository
from agent_orchestrator.services import (
    chat,
    compaction,
    history,
    memory_extraction,
    post_commit,
)
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.compaction import CompactionResult
from agent_orchestrator.services.conversations_queries import active_run
from agent_orchestrator.services.llm import LLMClient

logger = get_logger(__name__)


async def _prepare_chat_context(
    session: AsyncSession,
    llm: LLMClient,
    message: ChatMessage,
    conversation: Conversation,
    settings: Settings | None,
) -> chat.ChatPromptContext:
    """直接对话的上下文:压缩摘要 + 长期记忆召回(§6.7 记忆模块)。

    压缩只在超过阈值时真正调模型,失败/未开启时退回"近期原文"的老口径;
    记忆召回按工作区隔离,召回失败只降级为空——上下文增强不能阻断对话。
    """
    rows = await compaction.rows_before_current(
        session, message.tenant_id, message.conversation_id, message
    )
    summary, covered_until = await compaction.summary_for(
        session, message.tenant_id, message.conversation_id
    )
    # 压缩参数统一由 ContextPolicy 投影(P1-F §6.1):策略是唯一真源,
    # 配置项散读容易与执行图/管线里那份口径漂开
    compaction_policy = policy_from_settings(settings).compaction()
    if compaction_policy.enabled:
        result = await compaction.compact_conversation(
            session,
            message.tenant_id,
            message.conversation_id,
            llm,
            message=message,
            threshold_tokens=compaction_policy.threshold_tokens,
            keep_recent=compaction_policy.keep_recent_messages,
            summary_max_chars=compaction_policy.summary_max_chars,
        )
        if result is not None:
            summary, covered_until = result.summary, result.covered_until
    memory_block = ""
    if settings is not None and settings.memory_enabled:
        convention = await memory_service.convention_block(
            session,
            message.tenant_id,
            workspace_id=conversation.workspace_id,
            max_chars=settings.memory_convention_max_chars,
        )
        recall = await memory_service.recall_block(
            session,
            message.tenant_id,
            message.content,
            workspace_id=conversation.workspace_id,
            limit=settings.memory_recall_limit,
            max_chars=settings.memory_recall_max_chars,
        )
        memory_block = "\n\n".join(part for part in (convention, recall) if part)
    return chat.ChatPromptContext(
        rows=history.filter_after(rows, covered_until),
        summary=summary,
        memory_block=memory_block,
    )


async def _maybe_extract_memories(
    session: AsyncSession,
    llm: LLMClient | None,
    *,
    tenant_id: str,
    conversation_id: str,
    workspace_id: str | None,
    settings: Settings | None,
) -> None:
    """chat 回复落库后按累计条数节流地自动抽取长期记忆(P2)。

    节流语义:每累计 memory_extraction_min_messages 条消息抽一次(用户与
    assistant 都计入),同一对话不会每轮都多调一次模型;抽取失败只记 warning,
    绝不影响已经产出的回复(增强能力不能反噬主链路)。
    """
    if llm is None or settings is None:
        return
    if not (settings.memory_enabled and settings.memory_auto_extract_enabled):
        return
    min_messages = settings.memory_extraction_min_messages
    if min_messages <= 0:
        return
    try:
        repository = MessageRepository(session)
        count = await repository.count_for_conversation(tenant_id, conversation_id)
        if count % min_messages != 0:
            return
        messages = await repository.list_for_conversation(tenant_id, conversation_id)
        await memory_extraction.extract_from_messages(
            session,
            llm,
            tenant_id=tenant_id,
            messages=messages,
            workspace_id=workspace_id,
            source_type="chat",
            source_id=conversation_id,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - 记忆抽取不能影响回复
        logger.warning(
            "memory auto extraction failed",
            conversation_id=conversation_id,
            error=str(exc),
        )


def _schedule_chat_extraction(
    session: AsyncSession,
    llm: LLMClient | None,
    *,
    tenant_id: str,
    conversation_id: str,
    workspace_id: str | None,
    settings: Settings | None,
) -> bool:
    """有提交后回调能力时,把 chat 抽取推迟到事务提交后(P1-2)。

    返回 True 表示已登记回调、调用方不要再同步等待;返回 False 表示当前会话
    没有运行时绑定(服务层直调/单测),调用方保留同步语义。
    """
    runtime = post_commit.runtime_of(session)
    if runtime is None or llm is None or settings is None:
        return False
    if not (settings.memory_enabled and settings.memory_auto_extract_enabled):
        return False

    async def _extract() -> None:
        async with session_scope(runtime.session_factory) as extract_session:
            await _maybe_extract_memories(
                extract_session,
                llm,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                settings=settings,
            )

    post_commit.register_after_commit(session, _extract)
    return True


async def _task_compaction(
    session: AsyncSession,
    llm: LLMClient | None,
    conversation: Conversation,
    message: ChatMessage,
    settings: Settings | None,
) -> CompactionResult | None:
    """任务路径建 run 前的压缩(已有进行中 run 时并入指令队列,不需要)。

    压缩放在这里而不是执行图里:建 run 的请求边界有 LLM 客户端,压缩只发生
    一次;执行图只读已落库的摘要(见 run_lifecycle._conversation_history)。
    """
    cfg = policy_from_settings(settings).compaction()
    if not cfg.enabled or llm is None:
        return None
    if await active_run(session, conversation.tenant_id, conversation.id) is not None:
        return None
    return await compaction.compact_conversation(
        session,
        conversation.tenant_id,
        conversation.id,
        llm,
        message=message,
        threshold_tokens=cfg.threshold_tokens,
        keep_recent=cfg.keep_recent_messages,
        summary_max_chars=cfg.summary_max_chars,
    )
