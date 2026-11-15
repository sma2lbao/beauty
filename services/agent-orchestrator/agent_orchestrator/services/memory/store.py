"""记忆行级生命周期:写入/列举/读取/更新/删除/过期清理/导出/统计/重嵌入。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from common.errors import AppError, ConflictError, NotFoundError, UpstreamError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.repositories import MemoryRepository, MemoryReviewRepository
from agent_orchestrator.services.embeddings import (
    EmbeddingUnavailable,
    current_provider,
    embed_many,
    embed_one,
)
from agent_orchestrator.services.embeddings import (
    tokenize as _tokenize,
)

from .constants import (
    _CANDIDATE_LIMIT,
    _CONFLICT_KINDS,
    _PROCEDURE_KIND,
    LOGGER_NAME,
    MEMORY_INJECTION_MODES,
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MEMORY_SOURCE_TYPES,
)
from .scoring import (
    _as_utc,
    _lexical_overlap,
    _utcnow,
    content_similarity,
    find_sensitive,
)

logger = get_logger(LOGGER_NAME)


async def write_memory(
    session: AsyncSession,
    account_id: str,
    *,
    content: str,
    kind: str = "note",
    scope: str = "account",
    workspace_id: str | None = None,
    source_type: str = "manual",
    source_id: str | None = None,
    pinned: bool = False,
    ttl_days: int | None = None,
    default_ttl_days: int = 0,
    max_content_chars: int = 8000,
    max_entries: int = 2000,
    injection_mode: str = "retrieved",
    confidence: float = 1.0,
    valid_from: datetime | None = None,
    conflict_threshold: float = 0.0,
    reject_sensitive: bool = True,
) -> Memory:
    """写入一条长期记忆;参数非法或超账号上限抛 AppError(不静默截断语义)。

    TTL 语义:None = 采用 default_ttl_days(部署默认),0 = 永久,>0 = N 天后过期。
    内容按 max_content_chars 截断(用户长文笔记不至于写失败),其余校验直接报错;
    条数上限只统计"仍然有效"的记忆,过期/软删的不占额度。

    R5 时效:conflict_threshold>0 时,同 kind/同作用域的高相似旧条目会被标为
    invalid_at 并指向新条目(替代链保留);note/procedure 不参与替代。
    R8 安全:默认拒绝疑似凭据/银行卡的内容,避免敏感信息被反复注入。
    """
    text = (content or "").strip()
    if not text:
        raise AppError("memory.empty", "记忆内容不能为空")
    if kind not in MEMORY_KINDS:
        raise AppError("memory.invalid_kind", f"未知记忆类型: {kind}")
    if scope not in MEMORY_SCOPES:
        raise AppError("memory.invalid_scope", f"未知记忆作用域: {scope}")
    if source_type not in MEMORY_SOURCE_TYPES:
        raise AppError("memory.invalid_source", f"未知记忆来源: {source_type}")
    if injection_mode not in MEMORY_INJECTION_MODES:
        raise AppError("memory.invalid_injection", f"未知注入模式: {injection_mode}")
    if kind == _PROCEDURE_KIND and injection_mode == "always":
        raise AppError(
            "memory.invalid_injection",
            "procedure 已有常驻约定块,不能重复设为 always 注入",
        )
    if not 0.0 <= confidence <= 1.0:
        raise AppError("memory.invalid_confidence", "置信度必须在 0~1 之间")
    if scope == "workspace" and not workspace_id:
        raise AppError("memory.workspace_required", "工作区级记忆缺少 workspace_id")
    if scope == "account":
        workspace_id = None
    if max_content_chars > 0 and len(text) > max_content_chars:
        text = text[:max_content_chars]
    if ttl_days is None:
        ttl_days = default_ttl_days
    if ttl_days < 0:
        raise AppError("memory.invalid_ttl", "ttl_days 不能为负数")
    if reject_sensitive:
        category = find_sensitive(text)
        if category is not None:
            logger.warning(
                "memory sensitive rejected", account_id=account_id, category=category
            )
            raise AppError(
                "memory.sensitive_rejected",
                "记忆内容疑似包含敏感凭据,已拒绝写入",
                details={"category": category},
            )

    repository = MemoryRepository(session)
    if max_entries > 0:
        stats = await repository.counts(account_id)
        if stats["active"] >= max_entries:
            raise AppError(
                "memory.limit_reached",
                "记忆条数已达上限,请先删除或等待过期",
            )

    now = _utcnow()
    expires_at = now + timedelta(days=ttl_days) if ttl_days > 0 else None
    # 向量与模型名一起落库:换 provider 后历史行仍记得自己由哪个模型产出,
    # 检索侧遇到维度不一致会忽略向量分量而不是算出错误余弦。
    embedding, embedding_model = await embed_one(text)
    memory = Memory(
        account_id=account_id,
        workspace_id=workspace_id,
        scope=scope,
        kind=kind,
        content=text,
        embedding=embedding,
        embedding_model=embedding_model,
        source_type=source_type,
        source_id=source_id,
        pinned=pinned,
        expires_at=expires_at,
        injection_mode=injection_mode,
        confidence=confidence,
        valid_from=valid_from or now,
    )
    await repository.add(memory)

    if conflict_threshold > 0 and kind in _CONFLICT_KINDS:
        candidates = await repository.list_active(
            account_id,
            workspace_id=workspace_id if scope == "workspace" else None,
            kind=kind,
            scope=scope,
            limit=_CANDIDATE_LIMIT,
        )
        stale = [
            item.id
            for item in candidates
            if item.id != memory.id
            and item.workspace_id == workspace_id
            and content_similarity(text, item.content) >= conflict_threshold
        ]
        if stale:
            await repository.supersede(
                account_id, stale, superseded_by=memory.id, now=now
            )
            logger.info(
                "memory superseded",
                account_id=account_id,
                superseded_by=memory.id,
                count=len(stale),
            )
    return memory


async def list_memories(
    session: AsyncSession,
    account_id: str,
    *,
    workspace_id: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    source_type: str | None = None,
    limit: int = 50,
    include_invalid: bool = False,
) -> list[Memory]:
    """列出生效中的记忆(管理面用);include_invalid 含已被替代的条目。

    q 给定时按词面重合做二次过滤;管理面检索不写访问统计。
    """
    repository = MemoryRepository(session)
    text = (q or "").strip()
    if not text:
        return await repository.list_visible(
            account_id,
            workspace_id=workspace_id,
            kind=kind,
            source_type=source_type,
            include_invalid=include_invalid,
            limit=limit,
        )
    candidates = await repository.list_visible(
        account_id,
        workspace_id=workspace_id,
        kind=kind,
        source_type=source_type,
        include_invalid=include_invalid,
        limit=max(limit, _CANDIDATE_LIMIT),
    )
    query_tokens = set(_tokenize(text))
    matched = [
        memory
        for memory in candidates
        if _lexical_overlap(query_tokens, memory.content) > 0
    ]
    return matched[:limit]


async def get_memory(session: AsyncSession, account_id: str, memory_id: str) -> Memory:
    """取单条记忆(含已失效,便于查看替代链);软删/跨账号一律 404。"""
    memory = await MemoryRepository(session).get_visible(account_id, memory_id)
    if memory is None:
        raise NotFoundError(
            "memory.not_found", "记忆不存在", details={"memory_id": memory_id}
        )
    return memory


async def update_memory(
    session: AsyncSession,
    account_id: str,
    memory_id: str,
    *,
    content: str | None = None,
    kind: str | None = None,
    scope: str | None = None,
    workspace_id: str | None = None,
    workspace_id_set: bool = False,
    pinned: bool | None = None,
    ttl_days: int | None = None,
    ttl_days_set: bool = False,
    injection_mode: str | None = None,
    expected_updated_at: datetime | None = None,
    max_content_chars: int = 8000,
    reject_sensitive: bool = True,
) -> Memory:
    """局部更新一条记忆;语义校验后重算嵌入并落库。

    expected_updated_at 是乐观并发令牌:与当前 updated_at 不一致时返回 409,
    避免两个窗口互相覆盖(前端提交时带上次读到的值)。
    ttl_days_set=False 表示"不改有效期";True 时 0=永久、>0=N 天。
    """
    repository = MemoryRepository(session)
    memory = await repository.get_visible(account_id, memory_id)
    if memory is None:
        raise NotFoundError(
            "memory.not_found", "记忆不存在", details={"memory_id": memory_id}
        )
    if expected_updated_at is not None and memory.updated_at is not None:
        expected = _as_utc(expected_updated_at)
        current = _as_utc(memory.updated_at)
        if abs((current - expected).total_seconds()) >= 1e-6:
            raise ConflictError(
                "memory.conflict",
                "记忆已被修改,请刷新后重试",
                details={"memory_id": memory_id},
            )

    target_scope = scope or memory.scope
    if target_scope not in MEMORY_SCOPES:
        raise AppError("memory.invalid_scope", f"未知记忆作用域: {target_scope}")
    if target_scope == "workspace":
        target_workspace = workspace_id if workspace_id_set else memory.workspace_id
        if not target_workspace:
            raise AppError("memory.workspace_required", "工作区级记忆缺少 workspace_id")
    else:
        target_workspace = None

    target_kind = kind or memory.kind
    if target_kind not in MEMORY_KINDS:
        raise AppError("memory.invalid_kind", f"未知记忆类型: {target_kind}")
    target_injection = injection_mode or memory.injection_mode or "retrieved"
    if target_injection not in MEMORY_INJECTION_MODES:
        raise AppError("memory.invalid_injection", f"未知注入模式: {target_injection}")
    if target_kind == _PROCEDURE_KIND and target_injection == "always":
        raise AppError(
            "memory.invalid_injection",
            "procedure 已有常驻约定块,不能重复设为 always 注入",
        )

    target_content = memory.content
    if content is not None:
        target_content = content.strip()
        if not target_content:
            raise AppError("memory.empty", "记忆内容不能为空")
        if max_content_chars > 0 and len(target_content) > max_content_chars:
            target_content = target_content[:max_content_chars]
        if reject_sensitive:
            category = find_sensitive(target_content)
            if category is not None:
                logger.warning(
                    "memory sensitive rejected",
                    account_id=account_id,
                    category=category,
                )
                raise AppError(
                    "memory.sensitive_rejected",
                    "记忆内容疑似包含敏感凭据,已拒绝写入",
                    details={"category": category},
                )

    memory.scope = target_scope
    memory.workspace_id = target_workspace
    memory.kind = target_kind
    memory.injection_mode = target_injection
    if pinned is not None:
        memory.pinned = pinned
    if target_content != memory.content:
        memory.content = target_content
        memory.embedding, memory.embedding_model = await embed_one(target_content)
    if ttl_days_set:
        if ttl_days is None or ttl_days < 0:
            raise AppError("memory.invalid_ttl", "ttl_days 不能为负数")
        memory.expires_at = (
            _utcnow() + timedelta(days=ttl_days) if ttl_days > 0 else None
        )
    await session.flush()
    return memory


async def delete_memory(session: AsyncSession, account_id: str, memory_id: str) -> None:
    """软删除一条记忆;不存在或跨账号一律 404,不泄漏存在性。"""
    deleted = await MemoryRepository(session).soft_delete(account_id, memory_id)
    if not deleted:
        raise NotFoundError(
            "memory.not_found",
            "记忆不存在",
            details={"memory_id": memory_id},
        )


async def purge_expired(session: AsyncSession, account_id: str) -> int:
    """清理当前账号已过期的记忆,返回标记条数。"""
    return await MemoryRepository(session).purge_expired(account_id)


async def export_memories(
    session: AsyncSession,
    account_id: str,
    *,
    limit: int = 2000,
    include_invalid: bool = True,
) -> list[Memory]:
    """导出账号记忆(不含软删),用于备份/迁移/合规审计。"""
    return await MemoryRepository(session).list_visible(
        account_id, include_invalid=include_invalid, limit=limit
    )


async def purge_all_memories(session: AsyncSession, account_id: str) -> dict[str, int]:
    """彻底删除账号的全部记忆与复核队列(R8 合规删除,不可恢复)。

    与软删除不同:这里直接物理删行,调用方必须先做二次确认。
    """
    reviews = await MemoryReviewRepository(session).delete_all_for_account(account_id)
    memories = await MemoryRepository(session).delete_all_for_account(account_id)
    logger.info(
        "memories purged", account_id=account_id, memories=memories, reviews=reviews
    )
    return {"memories": memories, "reviews": reviews}


async def memory_stats(session: AsyncSession, account_id: str) -> dict[str, int]:
    """账号记忆计数(total/active/pinned/expired/superseded/pending_reviews)。"""
    stats = await MemoryRepository(session).counts(account_id)
    stats["pending_reviews"] = await MemoryReviewRepository(session).count_pending(
        account_id
    )
    return stats


async def reembed_memories(
    session: AsyncSession,
    account_id: str,
    *,
    batch: int = 200,
    dry_run: bool = False,
) -> dict[str, Any]:
    """按当前嵌入提供方重建"向量缺失/模型不一致"的记忆行。

    换嵌入模型(例如本地哈希升级到 bge-m3)后,旧行与新查询的向量不同源,
    检索会忽略向量分量只靠词面;重建后恢复混合打分。只更新 vector 与模型名,
    不动 updated_at/access_count——重建是维护动作,不该让"记忆看起来刚改过"。
    provider 不可用时原样报错(不回退本地):否则会把本地哈希向量写成
    语义模型名,反而污染数据。
    """
    provider = current_provider()
    repository = MemoryRepository(session)
    rows = await repository.list_for_reembed(
        account_id, model=provider.model, limit=max(1, batch)
    )
    result: dict[str, Any] = {
        "scanned": len(rows),
        "updated": 0,
        "skipped": 0,
        "model": provider.model,
        "dry_run": dry_run,
    }
    if dry_run or not rows:
        return result
    try:
        vectors, model = await embed_many(
            [row.content for row in rows], fallback_local=False
        )
    except EmbeddingUnavailable as exc:
        raise UpstreamError(
            "memory.embedding_unavailable",
            "嵌入服务暂不可用,请稍后重试",
        ) from exc
    updated = 0
    for row, vector in zip(rows, vectors, strict=True):
        if not vector:
            continue
        row.embedding = vector
        row.embedding_model = model
        updated += 1
    await session.flush()
    result["updated"] = updated
    result["skipped"] = len(rows) - updated
    logger.info(
        "memories reembedded",
        account_id=account_id,
        scanned=len(rows),
        updated=updated,
        model=model,
    )
    return result
