"""工具调用审计查询(账号内,供运维与对账)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.api.deps import get_account_id, get_session
from tool_service.repositories import ToolCallRepository, to_read
from tool_service.schemas.tools import (
    LIST_LIMIT_MAX,
    ToolCallListRead,
    ToolCallRead,
    ToolCancelBody,
    ToolCancelRead,
)
from tool_service.services.reservation import request_cancel

router = APIRouter(prefix="/tool-calls", tags=["tool-calls"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccountDep = Annotated[str, Depends(get_account_id)]


@router.get("", response_model=ToolCallListRead)
async def list_tool_calls(
    session: SessionDep,
    account_id: AccountDep,
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
        account_id,
        run_id=run_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        ok=ok,
        limit=limit,
        offset=offset,
    )
    total = await repository.count_page(
        account_id,
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


@router.post(":cancel", response_model=ToolCancelRead)
async def cancel_tool_calls(
    body: ToolCancelBody,
    session: SessionDep,
    account_id: AccountDep,
) -> ToolCancelRead:
    """给在飞调用置取消标记(S6);执行方在轮询间隔内放弃等待并结算 cancelled。

    只标记 status=running 的行:终态行取消没有意义,排队中的请求还没落行。
    已发到沙箱的命令不会被打断,本服务只是不再等它的结果(要真停用 job_kill)。
    """

    cancelled = await request_cancel(
        session,
        account_id=account_id,
        run_id=body.run_id,
        tool_call_id=body.tool_call_id,
    )
    return ToolCancelRead(cancelled=cancelled)


@router.get("/{tool_call_id}", response_model=ToolCallRead)
async def get_tool_call(
    tool_call_id: str,
    session: SessionDep,
    account_id: AccountDep,
    include_patch: Annotated[bool, Query()] = False,
) -> ToolCallRead:
    """单条审计;跨账号一律 404(仓储统一语义)。"""

    repository = ToolCallRepository(session)
    record = await repository.require_by_call_id(account_id, tool_call_id)
    return to_read(record, include_patch=include_patch)
