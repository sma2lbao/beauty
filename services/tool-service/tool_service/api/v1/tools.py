"""工具目录与执行端点。

执行错误(参数不合法、工具内部失败)走 HTTP 200 + 错误信封——它们是
模型可见的数据;只有未知工具(404)、需要人工确认(403)、幂等冲突(409)
和请求结构非法(422)才是 API 错误。
"""

from __future__ import annotations

from typing import Annotated, Any

from common.errors import ConflictError, NotFoundError
from common.tools import ToolError, ToolExecuteResponse
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.api.deps import (
    get_session,
    get_tenant_id,
    get_tool_runner,
)
from tool_service.models.tool_call import ToolCall
from tool_service.repositories import ToolCallRepository
from tool_service.schemas.tools import (
    ToolDescriptorRead,
    ToolExecuteBody,
    ToolListRead,
)
from tool_service.services.catalog import (
    catalog_version,
    descriptors,
    find_descriptor,
)
from tool_service.services.execution import (
    ExecutionContext,
    ToolRunner,
    arguments_digest,
    audit_columns,
)

router = APIRouter(prefix="/tools", tags=["tools"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[str, Depends(get_tenant_id)]
RunnerDep = Annotated[ToolRunner, Depends(get_tool_runner)]


@router.get("", response_model=ToolListRead)
async def list_tools(
    kind: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> ToolListRead:
    """列出启用的工具;kind=function|interaction、scope 可过滤。"""

    items = [
        tool
        for tool in descriptors()
        if (kind is None or str(tool.kind) == kind)
        and (scope is None or scope in tool.scopes)
    ]
    return ToolListRead(
        tools=[ToolDescriptorRead.from_descriptor(tool) for tool in items],
        catalog_version=catalog_version(),
    )


@router.get("/{name}", response_model=ToolDescriptorRead)
async def get_tool(name: str) -> ToolDescriptorRead:
    """按正式名或别名取单个工具描述。"""

    descriptor = find_descriptor(name)
    if descriptor is None or not descriptor.enabled:
        raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
    return ToolDescriptorRead.from_descriptor(descriptor)


@router.post("/{name}:execute")
async def execute_tool(
    request: Request,
    name: str,
    body: ToolExecuteBody,
    session: SessionDep,
    tenant_id: TenantDep,
    runner: RunnerDep,
) -> JSONResponse:
    """执行工具;tool_call_id 是幂等键(同参重放、异参 409)。"""

    descriptor = find_descriptor(name)
    if descriptor is None or not descriptor.enabled:
        raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
    repository = ToolCallRepository(session)
    digest = arguments_digest(body.arguments)
    existing = await repository.find_by_call_id(tenant_id, body.tool_call_id)
    if existing is not None:
        return _replay_response(existing, digest)
    context = ExecutionContext(
        tenant_id=tenant_id,
        tool_call_id=body.tool_call_id,
        run_id=body.run_id,
        conversation_id=body.conversation_id,
        workspace_id=body.workspace_id,
        sandbox_scope=body.sandbox_scope,
        timeout_seconds=body.timeout_seconds,
        approved=body.approved,
    )
    outcome = await runner.execute(context, descriptor.name, body.arguments)
    decision = "approved" if body.approved else "auto"
    columns = audit_columns(outcome, context, body.arguments, decision=decision)
    record = ToolCall(
        tenant_id=tenant_id,
        tool_call_id=body.tool_call_id,
        run_id=body.run_id,
        conversation_id=body.conversation_id,
        **columns,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        # 并发同 tool_call_id:后到者回滚并按已存在记录重放
        await session.rollback()
        existing = await repository.find_by_call_id(tenant_id, body.tool_call_id)
        if existing is None:
            raise
        return _replay_response(existing, digest)
    return JSONResponse(outcome.response.envelope())


def _replay_response(existing: ToolCall, digest: str) -> JSONResponse:
    """同一 tool_call_id 的第二次请求:参数一致则原样重放,否则 409。"""

    if existing.arguments_sha256 != digest:
        raise ConflictError(
            "tool.idempotency_conflict",
            "同一 tool_call_id 的参数与首次调用不一致",
            details={"tool_call_id": existing.tool_call_id},
        )
    error: ToolError | None = None
    result: dict[str, Any] | None = None
    if existing.ok:
        result = {
            "replayed": True,
            "note": "该 tool_call_id 已执行成功,本次未重复执行",
        }
    else:
        error = ToolError(
            code=existing.error_code or "tool.replayed",
            message="该 tool_call_id 已执行失败,本次未重复执行",
            retryable=False,
        )
    response = ToolExecuteResponse(
        ok=existing.ok,
        result=result,
        error=error,
        truncated=existing.truncated,
        duration_ms=existing.duration_ms,
        tool_call_id=existing.tool_call_id,
        idempotent_replay=True,
    )
    return JSONResponse(response.envelope())
