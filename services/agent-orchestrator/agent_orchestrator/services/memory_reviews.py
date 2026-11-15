"""低置信度记忆候选的复核队列(P3-B/R7)。

自动抽取默认关;开启后低置信度候选也不直接落库,而是先进入复核队列:
人工确认才成为长期记忆,拒绝则只留痕。这样模型臆造/被投毒的内容不会
无声进入召回,同时保留"抽取 → 审阅 → 采纳"的闭环。
"""

from __future__ import annotations

from common.errors import AppError, ConflictError, NotFoundError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.memory import Memory, MemoryReview
from agent_orchestrator.repositories import MemoryRepository, MemoryReviewRepository
from agent_orchestrator.services import memory as memory_service

#: 复核队列单次返回上限。
_REVIEW_LIMIT = 100
#: 入队前与既有记忆比对近重复的候选规模。
_DEDUPE_SCAN_LIMIT = 500

logger = get_logger(__name__)


async def queue_candidate(
    session: AsyncSession,
    account_id: str,
    *,
    content: str,
    kind: str = "note",
    scope: str = "account",
    workspace_id: str | None = None,
    confidence: float = 0.0,
    evidence_quote: str | None = None,
    source_type: str = "chat",
    source_id: str | None = None,
    dedupe_threshold: float = 0.9,
) -> MemoryReview | None:
    """把低置信度候选放进复核队列;敏感内容/近重复直接丢弃。

    返回 None 表示候选被过滤掉(不是错误):抽取是后台增强,单条不合格不应
    让整批失败。
    """
    text = (content or "").strip()
    if not text:
        return None
    if memory_service.find_sensitive(text) is not None:
        return None
    if scope not in memory_service.MEMORY_SCOPES:
        scope = "account"
    if kind not in memory_service.MEMORY_KINDS:
        kind = "note"
    target_workspace = workspace_id if scope == "workspace" else None
    if scope == "workspace" and not target_workspace:
        return None
    repository = MemoryReviewRepository(session)
    pending = await repository.list_pending(account_id, limit=_REVIEW_LIMIT)
    key = text.casefold()
    if any(
        item.kind == kind
        and item.workspace_id == target_workspace
        and item.content.strip().casefold() == key
        for item in pending
    ):
        return None
    # 已经存在的近重复记忆不必再让人复核一遍。
    existing = await MemoryRepository(session).list_active(
        account_id, limit=_DEDUPE_SCAN_LIMIT
    )
    for memory in existing:
        if memory.kind != kind:
            continue
        if (memory.workspace_id if memory.scope == "workspace" else None) != (
            target_workspace
        ):
            continue
        if memory_service.content_similarity(text, memory.content) >= dedupe_threshold:
            return None
    review = MemoryReview(
        account_id=account_id,
        workspace_id=target_workspace,
        scope=scope,
        kind=kind,
        content=text,
        confidence=max(0.0, min(1.0, confidence)),
        evidence_quote=(evidence_quote or "").strip()[:2000] or None,
        source_type=source_type,
        source_id=source_id,
        status="pending",
    )
    added = await repository.add(review)
    logger.info(
        "memory review queued",
        account_id=account_id,
        review_id=added.id,
        kind=added.kind,
        confidence=added.confidence,
    )
    return added


async def list_reviews(
    session: AsyncSession, account_id: str, *, limit: int = 50
) -> list[MemoryReview]:
    """列出待复核候选(最新在前)。"""
    return await MemoryReviewRepository(session).list_pending(
        account_id, limit=max(1, min(limit, _REVIEW_LIMIT))
    )


async def resolve_review(
    session: AsyncSession,
    account_id: str,
    review_id: str,
    *,
    approve: bool,
    note: str | None = None,
    default_ttl_days: int = 0,
    max_entries: int = 2000,
    max_content_chars: int = 8000,
    conflict_threshold: float = 0.0,
) -> Memory | None:
    """通过/拒绝一条复核候选;通过时按候选内容写入长期记忆。

    幂等:已处理的复核项再次提交返回 409 语义由调用方决定;这里直接拒绝,
    避免同一候选被重复采纳。
    """
    repository = MemoryReviewRepository(session)
    review = await repository.get_for_account(account_id, review_id)
    if review is None:
        raise NotFoundError(
            "memory.review_not_found",
            "记忆复核项不存在",
            details={"review_id": review_id},
        )
    if review.status != "pending":
        raise ConflictError(
            "memory.review_resolved",
            "该复核项已处理",
            details={"review_id": review_id, "status": review.status},
        )
    memory: Memory | None = None
    if approve:
        try:
            memory = await memory_service.write_memory(
                session,
                account_id,
                content=review.content,
                kind=review.kind,
                scope=review.scope,
                workspace_id=review.workspace_id,
                source_type="manual",
                source_id=review.id,
                confidence=review.confidence or 0.5,
                max_content_chars=max_content_chars,
                max_entries=max_entries,
                default_ttl_days=default_ttl_days,
                conflict_threshold=conflict_threshold,
            )
        except AppError as exc:
            if exc.code == "memory.sensitive_rejected":
                review.status = "rejected"
                review.resolution_note = f"自动拒绝: {exc.message}"
                await session.flush()
                logger.warning("memory review auto rejected", review_id=review.id)
                return None
            raise
    review.status = "approved" if approve else "rejected"
    review.resolved_memory_id = memory.id if memory is not None else None
    review.resolution_note = (note or "").strip()[:2000] or None
    await session.flush()
    logger.info(
        "memory review resolved",
        account_id=account_id,
        review_id=review.id,
        approved=approve,
        memory_id=review.resolved_memory_id,
    )
    return memory
