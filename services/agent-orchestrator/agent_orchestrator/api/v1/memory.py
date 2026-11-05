"""长期记忆端点(记忆能力一期)。

契约:

- GET /memories:生效中的记忆列表(可按工作区/类型过滤,q 做词面过滤);
- POST /memories:写一条记忆(默认账号级;ttl_days>0 时到期自动不可见);
- GET /memories/search:相关性检索(命中会累加访问统计);
- GET /memories/stats:total/active/pinned/expired 计数;
- POST /memories/purge-expired:清理过期记忆(软删),返回条数;
- DELETE /memories/{memory_id}:软删除,跨账号一律 404(不泄漏存在性)。

注意路由顺序:/search、/stats、/purge-expired 必须先于 /{memory_id} 声明,
否则会被路径参数捕获。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_account_id, get_session, get_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.memory import (
    MemoryCreate,
    MemoryKind,
    MemoryPurgeResult,
    MemoryRead,
    MemorySearchResponse,
    MemoryStatsRead,
)
from agent_orchestrator.services import memory as memory_service

router = APIRouter(prefix="/memories", tags=["memories"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
WorkspaceQuery = Annotated[str | None, Query(max_length=32)]
KindQuery = Annotated[MemoryKind | None, Query()]


@router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    session: DBSession,
    account_id: AccountId,
    query: Annotated[str, Query(min_length=1, max_length=2000)],
    workspace_id: WorkspaceQuery = None,
    kind: KindQuery = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> MemorySearchResponse:
    """按相关性检索长期记忆;无命中返回空列表(不报 404)。"""
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


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    session: DBSession,
    account_id: AccountId,
    workspace_id: WorkspaceQuery = None,
    kind: KindQuery = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MemoryRead]:
    """列出仍然有效的记忆;workspace_id 给定时含账号级 + 该工作区级两类。"""
    rows = await memory_service.list_memories(
        session,
        account_id,
        workspace_id=workspace_id,
        kind=kind,
        q=q,
        limit=limit,
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

    ttl_days 未显式给(或给 0)时用部署配置的默认有效期(0 = 永久);
    条数上限也取配置,避免 API 与后台抽取各用一套额度。
    """
    ttl_days = payload.ttl_days or settings.memory_default_ttl_days
    memory = await memory_service.write_memory(
        session,
        account_id,
        content=payload.content,
        kind=payload.kind,
        scope=payload.scope,
        workspace_id=payload.workspace_id,
        pinned=payload.pinned,
        ttl_days=ttl_days,
        max_entries=settings.memory_max_entries_per_account,
    )
    return MemoryRead.model_validate(memory)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    session: DBSession,
    account_id: AccountId,
) -> None:
    """软删除一条记忆;不存在或跨账号一律 404 memory.not_found。"""
    await memory_service.delete_memory(session, account_id, memory_id)
