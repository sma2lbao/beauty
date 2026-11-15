"""长期记忆端点(记忆能力一期 + P3-B 生命周期/复核)。

契约:

- GET /memories:生效中的记忆列表(可按工作区/类型/来源过滤,q 做词面过滤;
  include_invalid=true 时含已被替代的条目);
- POST /memories:写一条记忆(默认账号级;ttl_days 不传=部署默认,0=永久,>0=N 天后过期;
  injection_mode=always 表示常驻核心块);
- GET /memories/{id}:取单条(含已失效/已过期,便于查看替代链);
- PATCH /memories/{id}:局部更新(内容/类型/作用域/置顶/有效期/注入模式);
  带 expected_updated_at 做乐观并发,冲突返回 409 memory.conflict;
- GET /memories/search:只读相关性检索(不写访问统计,供管理面预览);
- POST /memories/search:检索并累加访问统计(真实召回/工具场景);
- GET /memories/stats:total/active/pinned/expired/superseded/pending_reviews 计数;
- POST /memories/purge-expired:清理过期记忆(软删),返回条数;
- GET /memories/reviews:待复核候选(低置信度自动抽取);
- POST /memories/reviews/{review_id}/approve|reject:通过(落库)或拒绝;
- GET /memories/export:导出账号全部记忆(P3-B 可携带性);
- POST /memories/import:批量导入(吃 export 的 memories 数组,近重复跳过、支持 dry_run);
- POST /memories/reembed:按当前嵌入模型重建向量缺失/不一致的历史行;
- DELETE /memories:彻底删除账号全部记忆与复核队列(需 confirm=true);
- DELETE /memories/{id}:软删除,跨账号一律 404(不泄漏存在性)。

注意路由顺序:/search、/stats、/purge-expired、/reviews、/export、/import、
/reembed 必须先于 /{memory_id} 声明,否则会被路径参数捕获。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from common.errors import AppError
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_account_id, get_session, get_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.memory import (
    MemoryCreate,
    MemoryExport,
    MemoryImportRequest,
    MemoryImportResult,
    MemoryKind,
    MemoryPurgeAllResult,
    MemoryPurgeResult,
    MemoryRead,
    MemoryReembedRequest,
    MemoryReembedResult,
    MemoryReviewDecision,
    MemoryReviewRead,
    MemorySearchRequest,
    MemorySearchResponse,
    MemoryStatsRead,
    MemoryUpdate,
)
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services import memory_reviews

router = APIRouter(prefix="/memories", tags=["memories"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
WorkspaceQuery = Annotated[str | None, Query(max_length=32)]
KindQuery = Annotated[MemoryKind | None, Query()]
SourceQuery = Annotated[str | None, Query(max_length=16)]


@router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    session: DBSession,
    account_id: AccountId,
    query: Annotated[str, Query(min_length=1, max_length=2000)],
    workspace_id: WorkspaceQuery = None,
    kind: KindQuery = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> MemorySearchResponse:
    """按相关性检索长期记忆;无命中返回空列表(不报 404)。

    只读接口:不累加 access_count,避免缓存/审计网关的重复 GET 污染访问统计。
    需要计数时用 POST /memories/search。
    """
    hits = await memory_service.search_memories(
        session,
        account_id,
        query,
        workspace_id=workspace_id,
        kind=kind,
        limit=limit,
    )
    return MemorySearchResponse(
        query=query,
        hits=[MemoryRead.model_validate(hit) for hit in hits],
    )


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories_with_access(
    payload: MemorySearchRequest,
    session: DBSession,
    account_id: AccountId,
) -> MemorySearchResponse:
    """检索长期记忆并记录访问统计(供真实召回/工具调用复用同一契约)。"""
    hits = await memory_service.search_memories(
        session,
        account_id,
        payload.query,
        workspace_id=payload.workspace_id,
        kind=payload.kind,
        limit=payload.limit,
        touch=True,
    )
    return MemorySearchResponse(
        query=payload.query,
        hits=[MemoryRead.model_validate(hit) for hit in hits],
    )


@router.get("/stats", response_model=MemoryStatsRead)
async def get_memory_stats(
    session: DBSession,
    account_id: AccountId,
) -> MemoryStatsRead:
    """账号维度记忆计数(控制台概览用)。"""
    stats = await memory_service.memory_stats(session, account_id)
    return MemoryStatsRead(**stats)


@router.post("/purge-expired", response_model=MemoryPurgeResult)
async def purge_expired_memories(
    session: DBSession,
    account_id: AccountId,
) -> MemoryPurgeResult:
    """把已过期记忆标记为删除(幂等,可重复调用)。"""
    purged = await memory_service.purge_expired(session, account_id)
    return MemoryPurgeResult(purged=purged)


@router.get("/reviews", response_model=list[MemoryReviewRead])
async def list_memory_reviews(
    session: DBSession,
    account_id: AccountId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[MemoryReviewRead]:
    """列出待复核候选(低置信度自动抽取);通过/拒绝后移出队列。"""
    reviews = await memory_reviews.list_reviews(session, account_id, limit=limit)
    return [MemoryReviewRead.model_validate(item) for item in reviews]


@router.post(
    "/reviews/{review_id}/approve",
    response_model=MemoryRead,
    status_code=status.HTTP_201_CREATED,
)
async def approve_memory_review(
    review_id: str,
    payload: MemoryReviewDecision,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> MemoryRead:
    """通过复核:按候选内容写入长期记忆(敏感内容会被自动拒绝并留痕)。"""
    memory = await memory_reviews.resolve_review(
        session,
        account_id,
        review_id,
        approve=True,
        note=payload.note,
        default_ttl_days=settings.memory_default_ttl_days,
        max_entries=settings.memory_max_entries_per_account,
        max_content_chars=settings.memory_max_content_chars,
        conflict_threshold=settings.memory_conflict_threshold,
    )
    if memory is None:
        # 自动拒绝(敏感内容):复核项已处理,但不会产生记忆。
        raise AppError(
            "memory.review_rejected",
            "复核内容疑似包含敏感信息,已自动拒绝",
            details={"review_id": review_id},
        )
    return MemoryRead.model_validate(memory)


@router.post("/reviews/{review_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_memory_review(
    review_id: str,
    payload: MemoryReviewDecision,
    session: DBSession,
    account_id: AccountId,
) -> None:
    """拒绝复核候选:只留痕,不写入长期记忆。"""
    await memory_reviews.resolve_review(
        session, account_id, review_id, approve=False, note=payload.note
    )


@router.get("/export", response_model=MemoryExport)
async def export_memories(
    session: DBSession,
    account_id: AccountId,
) -> MemoryExport:
    """导出账号全部记忆(含失效条目),用于迁移/备份/可携带性。"""
    memories = await memory_service.export_memories(session, account_id)
    return MemoryExport(
        exported_at=datetime.now(UTC),
        memories=[MemoryRead.model_validate(item) for item in memories],
    )


@router.post("/import", response_model=MemoryImportResult)
async def import_memories(
    payload: MemoryImportRequest,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> MemoryImportResult:
    """批量导入记忆:迁移/恢复/外部导入;近重复跳过,支持 dry_run 预览。

    导入是补数据而不是表达新事实,因此不触发新旧事实替代;敏感内容按配置拒绝。
    """
    result = await memory_service.import_memories(
        session,
        account_id,
        [item.model_dump() for item in payload.memories],
        dry_run=payload.dry_run,
        max_content_chars=settings.memory_max_content_chars,
        max_entries=settings.memory_max_entries_per_account,
        dedupe_threshold=settings.memory_dedupe_threshold,
        reject_sensitive=settings.memory_sensitive_filter_enabled,
    )
    return MemoryImportResult(**result)


@router.post("/reembed", response_model=MemoryReembedResult)
async def reembed_memories(
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
    payload: MemoryReembedRequest | None = None,
) -> MemoryReembedResult:
    """按当前嵌入提供方重建向量:换模型后恢复混合打分。

    默认只处理一批(memory_reembed_batch),重复调用可渐进完成;dry_run 只统计。
    """
    body = payload or MemoryReembedRequest()
    result = await memory_service.reembed_memories(
        session,
        account_id,
        batch=body.limit or settings.memory_reembed_batch,
        dry_run=body.dry_run,
    )
    return MemoryReembedResult(**result)


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    session: DBSession,
    account_id: AccountId,
    workspace_id: WorkspaceQuery = None,
    kind: KindQuery = None,
    source_type: SourceQuery = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    include_invalid: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MemoryRead]:
    """列出记忆;workspace_id 给定时含账号级 + 该工作区级两类。"""
    rows = await memory_service.list_memories(
        session,
        account_id,
        workspace_id=workspace_id,
        kind=kind,
        source_type=source_type,
        q=q,
        limit=limit,
        include_invalid=include_invalid,
    )
    return [MemoryRead.model_validate(row) for row in rows]


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def create_memory(
    payload: MemoryCreate,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> MemoryRead:
    """写入一条记忆;内容为空/类型非法/超条数上限由统一错误模型返回。

    ttl_days=None 用部署默认有效期,0 表示永久,>0 表示 N 天;
    条数上限也取配置,避免 API 与后台抽取各用一套额度。
    写入时按配置做敏感信息过滤与新旧事实替代。
    """
    memory = await memory_service.write_memory(
        session,
        account_id,
        content=payload.content,
        kind=payload.kind,
        scope=payload.scope,
        workspace_id=payload.workspace_id,
        pinned=payload.pinned,
        ttl_days=payload.ttl_days,
        default_ttl_days=settings.memory_default_ttl_days,
        max_entries=settings.memory_max_entries_per_account,
        max_content_chars=settings.memory_max_content_chars,
        injection_mode=payload.injection_mode,
        reject_sensitive=settings.memory_sensitive_filter_enabled,
        conflict_threshold=settings.memory_conflict_threshold,
    )
    return MemoryRead.model_validate(memory)


@router.get("/{memory_id}", response_model=MemoryRead)
async def get_memory(
    memory_id: str,
    session: DBSession,
    account_id: AccountId,
) -> MemoryRead:
    """取单条记忆(含已失效,便于查看替代链);软删/跨账号 404。"""
    memory = await memory_service.get_memory(session, account_id, memory_id)
    return MemoryRead.model_validate(memory)


@router.patch("/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: str,
    payload: MemoryUpdate,
    session: DBSession,
    account_id: AccountId,
    settings: SettingsDep,
) -> MemoryRead:
    """局部更新记忆;未提供字段保持不变。

    乐观并发:带 expected_updated_at 时,若已被他人修改返回 409 memory.conflict。
    ttl_days 只在显式出现时生效(0=永久,>0=N 天),避免漏传被误当作"改成永久"。
    """
    fields = payload.model_fields_set
    memory = await memory_service.update_memory(
        session,
        account_id,
        memory_id,
        content=payload.content,
        kind=payload.kind,
        scope=payload.scope,
        workspace_id=payload.workspace_id,
        workspace_id_set="workspace_id" in fields,
        pinned=payload.pinned,
        ttl_days=payload.ttl_days,
        ttl_days_set="ttl_days" in fields,
        injection_mode=payload.injection_mode,
        expected_updated_at=payload.expected_updated_at,
        max_content_chars=settings.memory_max_content_chars,
        reject_sensitive=settings.memory_sensitive_filter_enabled,
    )
    return MemoryRead.model_validate(memory)


@router.delete("", response_model=MemoryPurgeAllResult)
async def purge_all_memories(
    session: DBSession,
    account_id: AccountId,
    confirm: bool = Query(default=False),
) -> MemoryPurgeAllResult:
    """彻底删除账号全部记忆与复核队列(物理删除,不可恢复)。"""
    if not confirm:
        raise AppError("memory.confirm_required", "彻底删除需要 confirm=true 显式确认")
    result = await memory_service.purge_all_memories(session, account_id)
    return MemoryPurgeAllResult(**result)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    session: DBSession,
    account_id: AccountId,
) -> None:
    """软删除一条记忆;不存在或跨账号一律 404 memory.not_found。"""
    await memory_service.delete_memory(session, account_id, memory_id)
