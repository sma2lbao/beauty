"""工具目录与执行端点。

执行错误(参数不合法、工具内部失败)走 HTTP 200 + 错误信封——它们是
模型可见的数据;只有未知工具(404)、需要人工确认(403)、幂等冲突(409)
和请求结构非法(422)才是 API 错误。
"""

from __future__ import annotations

from typing import Annotated, Any

from common.errors import ConflictError, NotFoundError, PermissionDeniedError
from common.tools import ToolDescriptor, ToolError, ToolExecuteResponse, ToolRisk
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.api.deps import (
    get_session,
    get_settings,
    get_tenant_id,
    get_tool_runner,
)
from tool_service.core.config import Settings
from tool_service.models.tool_call import ToolCall
from tool_service.repositories import FileReadRepository, ToolCallRepository
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
    ExecutionOutcome,
    ToolRunner,
    arguments_digest,
    audit_columns,
    failure_outcome,
    is_available,
)
from tool_service.services.quota import check_total_calls, check_write_quota
from tool_service.services.sandbox import scope_key_for

router = APIRouter(prefix="/tools", tags=["tools"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[str, Depends(get_tenant_id)]
RunnerDep = Annotated[ToolRunner, Depends(get_tool_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=ToolListRead)
async def list_tools(
    settings: SettingsDep,
    kind: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> ToolListRead:
    """列出启用的工具;kind=function|interaction、scope 可过滤。"""

    items = [
        tool
        for tool in descriptors()
        if is_available(tool.name, settings)
        and (kind is None or str(tool.kind) == kind)
        and (scope is None or scope in tool.scopes)
    ]
    return ToolListRead(
        tools=[ToolDescriptorRead.from_descriptor(tool) for tool in items],
        catalog_version=catalog_version(),
    )


@router.get("/{name}", response_model=ToolDescriptorRead)
async def get_tool(name: str, settings: SettingsDep) -> ToolDescriptorRead:
    """按正式名或别名取单个工具描述。"""

    descriptor = find_descriptor(name)
    if (
        descriptor is None
        or not descriptor.enabled
        or not is_available(descriptor.name, settings)
    ):
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
    settings: SettingsDep,
) -> JSONResponse:
    """执行工具;tool_call_id 是幂等键(同参重放、异参 409)。

    执行前依次做:单 run 调用次数/写配额兜底、读前置校验(P1);
    这些拒绝也是模型可见的工具错误(HTTP 200 + ok=false),并照样落审计。
    """

    descriptor = find_descriptor(name)
    if descriptor is None or not descriptor.enabled:
        raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
    # 能力开关与目录一致:关闭的工具(exec/fetch)连执行入口都不存在。
    if not is_available(descriptor.name, settings):
        raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
    repository = ToolCallRepository(session)
    digest = arguments_digest(body.arguments)
    existing = await repository.find_by_call_id(tenant_id, body.tool_call_id)
    if existing is not None:
        return _replay_response(existing, digest)
    # 防御性风险下限:WRITE/DESTRUCTIVE 没有 approved=true 一律 403。
    # orchestrator 只在审批策略自动放行或人工确认后才传 approved=true。
    if descriptor.risk in (ToolRisk.WRITE, ToolRisk.DESTRUCTIVE) and not body.approved:
        raise PermissionDeniedError(
            "tool.confirmation_required",
            f"工具 {descriptor.name} 需要人工确认后才能执行",
        )
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
    outcome, internal_arguments = await _preflight(
        session, settings, descriptor, context, body.arguments
    )
    if outcome is None:
        outcome = await runner.execute(
            context,
            descriptor.name,
            body.arguments,
            internal_arguments=internal_arguments,
        )
        await _remember_read(session, descriptor, context, body, outcome)
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


async def _preflight(
    session: AsyncSession,
    settings: Settings,
    descriptor: ToolDescriptor,
    context: ExecutionContext,
    arguments: dict[str, Any],
) -> tuple[ExecutionOutcome | None, dict[str, Any] | None]:
    """执行前校验;返回(拒绝结果, None)或(None, helper 附加参数)。"""

    repository = ToolCallRepository(session)
    if context.run_id:
        used_calls = await repository.count_page(
            context.tenant_id, run_id=context.run_id
        )
        error = check_total_calls(
            used_calls=used_calls, limit=settings.max_tool_calls_per_run
        )
        if error is not None:
            return _reject(descriptor, context, error), None
    internal: dict[str, Any] | None = None
    if descriptor.risk is ToolRisk.WRITE:
        if context.run_id:
            used_writes, used_bytes = await repository.write_usage_for_run(
                context.tenant_id, context.run_id
            )
            error = check_write_quota(
                used_writes=used_writes,
                used_bytes=used_bytes,
                incoming_bytes=_incoming_bytes(descriptor, arguments),
                max_writes=settings.max_writes_per_run,
                max_bytes=settings.max_write_bytes_per_run,
            )
            if error is not None:
                return _reject(descriptor, context, error), None
        if settings.read_before_write_enabled:
            internal = await _read_gate(session, context, descriptor, arguments)
            if internal is _READ_REQUIRED:
                return (
                    _reject(
                        descriptor,
                        context,
                        ToolError(
                            code="tool.read_required",
                            message="写之前必须先 read 目标文件",
                            hint=(
                                "先用 read 查看 path(会记录读取版本),再执行 edit;"
                                "新建文件请直接用 write(目标不存在时无需先读)"
                            ),
                            retryable=False,
                        ),
                    ),
                    None,
                )
    return None, internal


#: _read_gate 的哨兵:需要拒绝执行(数据类无法用 None 表达「读前置未通过」)。
_READ_REQUIRED = object()


async def _read_gate(
    session: AsyncSession,
    context: ExecutionContext,
    descriptor: ToolDescriptor,
    arguments: dict[str, Any],
) -> dict[str, Any] | object | None:
    """读前置校验:改已有文件必须先读过;新建文件免读。

    读到过 → 返回读到的 sha256,由 write/edit 的乐观锁保证「不是盲改」;
    没读到过时,只对 `write` 放行并附内部标记 `_read_gate_absent`:沙箱内的
    write 执行器会确认目标确实不存在,已存在则回 `tool.read_required`。
    不存在的文件永远读不出 `file_reads` 记录,若不这样放行,新建文件会与
    read(not_found) 互相锁死;放行的安全性由执行器的存在性检查兜底。
    """

    if context.run_id is None and context.conversation_id is None:
        return None
    path = _normalize_path(arguments.get("path"))
    if path is None:
        return None  # 参数不合法:交给 validate_arguments 报 tool.invalid_args
    scope_key = scope_key_for(
        context.tenant_id,
        sandbox_scope=context.sandbox_scope,
        conversation_id=context.conversation_id,
        run_id=context.run_id,
    )
    record = await FileReadRepository(session).find_read(
        context.tenant_id, scope_key=scope_key, path=path
    )
    if record is not None:
        return {"_expected_read_sha256": record.sha256}
    if descriptor.name == "write":
        return {"_read_gate_absent": True}
    return _READ_REQUIRED


async def _remember_read(
    session: AsyncSession,
    descriptor: ToolDescriptor,
    context: ExecutionContext,
    body: ToolExecuteBody,
    outcome: ExecutionOutcome,
) -> None:
    """read 成功后记录 path+sha256,供后续 write/edit 的读前置校验。"""

    if descriptor.name != "read" or not outcome.response.ok:
        return
    result = outcome.response.result or {}
    path = _normalize_path(result.get("path")) or _normalize_path(
        body.arguments.get("path")
    )
    sha256 = result.get("sha256")
    if path is None or not isinstance(sha256, str):
        return
    scope_key = scope_key_for(
        context.tenant_id,
        sandbox_scope=context.sandbox_scope,
        conversation_id=context.conversation_id,
        run_id=context.run_id,
    )
    await FileReadRepository(session).upsert_read(
        context.tenant_id,
        scope_key=scope_key,
        path=path,
        sha256=sha256,
        run_id=context.run_id,
        conversation_id=context.conversation_id,
    )


def _reject(
    descriptor: ToolDescriptor, context: ExecutionContext, error: ToolError
) -> ExecutionOutcome:
    return failure_outcome(
        descriptor, context, error.code, error.message, hint=error.hint
    )


def _incoming_bytes(descriptor: ToolDescriptor, arguments: dict[str, Any]) -> int:
    """write 的待写字节数(edit 结果长度未知,按 0 估,由下一次调用兜底)。"""

    if descriptor.name != "write":
        return 0
    content = arguments.get("content")
    return len(content.encode("utf-8")) if isinstance(content, str) else 0


def _normalize_path(value: Any) -> str | None:
    """工具参数/结果里的相对路径归一化(去 ./、反斜杠转 /、折叠重复斜杠)。"""

    if not isinstance(value, str):
        return None
    text = value.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = "/".join(part for part in text.split("/") if part not in ("", "."))
    return text or None


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
