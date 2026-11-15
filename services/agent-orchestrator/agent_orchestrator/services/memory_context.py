"""记忆块的上下文注入组装(对话与 run 共用同一顺序/预算)。

注入顺序固定为:核心块(always)→ 程序性约定(procedure)→ 相关性召回。
集中在一处是为了让 HTTP 对话和 run 执行两条链路表现一致:任何一处顺序或
预算改动都会同时生效,避免"同一段记忆在两条链路上不一样"。

远程 provider 只接管"相关性召回"这一段(核心块/约定仍读本地表):远程失败
时回退本地并只记 warning——上下文增强不该阻断对话,也不该让用户看到 failed。
"""

from __future__ import annotations

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.memory_remote import MemoryProviderUnavailable

logger = get_logger(__name__)


async def prompt_memory_block(
    session: AsyncSession,
    account_id: str,
    query: str,
    *,
    workspace_id: str | None,
    settings: Settings,
) -> str:
    """按统一顺序组装记忆提示块;没有命中时返回空串(调用方按需拼接)。"""
    if not settings.memory_enabled:
        return ""
    core_rows = await memory_service.core_memories(
        session, account_id, workspace_id=workspace_id
    )
    core = memory_service.core_block_from(
        core_rows, max_chars=settings.memory_core_max_chars
    )
    convention = await memory_service.convention_block(
        session,
        account_id,
        workspace_id=workspace_id,
        max_chars=settings.memory_convention_max_chars,
    )
    recall = await _recall_block(
        session,
        account_id,
        query,
        workspace_id=workspace_id,
        settings=settings,
        exclude_ids={row.id for row in core_rows},
    )
    return "\n\n".join(part for part in (core, convention, recall) if part)


async def _recall_block(
    session: AsyncSession,
    account_id: str,
    query: str,
    *,
    workspace_id: str | None,
    settings: Settings,
    exclude_ids: set[str],
) -> str:
    """相关性召回:远程 provider 优先,失败回退本地;空查询直接跳过。"""
    text = (query or "").strip()
    if not text:
        return ""
    provider = memory_service.current_memory_provider()
    if not isinstance(provider, memory_service.LocalMemoryProvider):
        try:
            return await provider.recall(
                session,
                account_id,
                text,
                workspace_id=workspace_id,
                limit=settings.memory_recall_limit,
                max_chars=settings.memory_recall_max_chars,
            )
        except MemoryProviderUnavailable as exc:
            logger.warning(
                "memory provider recall failed",
                account_id=account_id,
                error=f"{type(exc).__name__}: {exc}",
            )
    return await memory_service.recall_block(
        session,
        account_id,
        text,
        workspace_id=workspace_id,
        limit=settings.memory_recall_limit,
        max_chars=settings.memory_recall_max_chars,
        exclude_ids=exclude_ids,
    )
