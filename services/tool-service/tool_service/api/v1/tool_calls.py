"""工具调用审计查询(租户内,供运维与对账)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.api.deps import get_session, get_tenant_id
from tool_service.repositories import ToolCallRepository, to_read
from tool_service.schemas.tools import (
    LIST_LIMIT_MAX,
    ToolCallListRead,
    ToolCallRead,
)

router = APIRouter(prefix="/tool-calls", tags=["tool-calls"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[str, Depends(get_tenant_id)]


@router.get("", response_model=ToolCallListRead)
async def list_tool_calls(
    session: SessionDep,
    tenant_id: TenantDep,
    run_id: Annotated[str | None, Query(max_length=32)] = None,
    conversation_id: Annotated[str | None, Query(max_length=32)] = None,
    tool_name: Annotated[str | None, Query(max_length=64)] = None,
    ok: Annotated[bool | None, Query()] = None,
    include_patch: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ToolCallListRead:
    """按 created_at 倒序分页;过滤条件都可选。

    conversation_id 用于跨 run 聚合(编排器的会话级工具调用视图);
    include_patch=false(默认)时 patch 为 null,行数/截断标记照常返回。
    """

    repository = ToolCallRepository(session)
    rows = await repository.list_page(
        tenant_id,
        run_id=run_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        ok=ok,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_page(
        tenant_id,
        run_id=run_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        ok=ok,
    )
    return ToolCallListRead(
        items=[to_read(row, include_patch=include_patch) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{tool_call_id}", response_model=ToolCallRead)
async def get_tool_call(
    tool_call_id: str,
    session: SessionDep,
    tenant_id: TenantDep,
    include_patch: Annotated[bool, Query()] = False,
) -> ToolCallRead:
    """单条审计;跨租户一律 404(仓储统一语义)。"""

    repository = ToolCallRepository(session)
    record = await repository.require_by_call_id(tenant_id, tool_call_id)
    return to_read(record, include_patch=include_patch)
