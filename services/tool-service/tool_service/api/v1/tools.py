"""工具目录与执行端点。

执行错误(参数不合法、工具内部失败)走 HTTP 200 + 错误信封——它们是
模型可见的数据;只有未知工具(404)、需要人工确认(403)、幂等冲突(409)
和请求结构非法(422)才是 API 错误。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

from common.errors import ConflictError, NotFoundError, PermissionDeniedError
from common.tools import ToolDescriptor, ToolError, ToolExecuteResponse, ToolRisk
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.api.deps import (
    get_account_id,
    get_connector_gateway,
    get_optional_account_id,
    get_session,
    get_settings,
    get_tool_runner,
)
from tool_service.core.config import Settings
from tool_service.models.tool_call import (
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    TERMINAL_STATUSES,
    ToolCall,
)
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
from tool_service.services.connectors import ConnectorGateway
from tool_service.services.execution import (
    ExecutionContext,
    ExecutionOutcome,
    ToolRunner,
    arguments_digest,
    audit_columns,
    failure_outcome,
    finish_tool_span,
    is_available,
    reservation_columns,
    tool_span,
)
from tool_service.services.quota import check_total_calls, check_write_quota
from tool_service.services.reservation import (
    CANCELLED_CODE,
    CANCELLED_MESSAGE,
    IN_PROGRESS_CODE,
    IN_PROGRESS_MESSAGE,
    ToolCallCancelled,
    await_with_cancel,
    cancel_requested,
    lease_deadline,
    settle,
    takeover,
)
from tool_service.services.sandbox import scope_key_for

router = APIRouter(prefix="/tools", tags=["tools"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccountDep = Annotated[str, Depends(get_account_id)]
OptionalAccountDep = Annotated[str | None, Depends(get_optional_account_id)]
RunnerDep = Annotated[ToolRunner, Depends(get_tool_runner)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnectorDep = Annotated[ConnectorGateway | None, Depends(get_connector_gateway)]


@router.get("", response_model=ToolListRead)
async def list_tools(
    settings: SettingsDep,
    connector: ConnectorDep,
    account_id: OptionalAccountDep,
    kind: Annotated[str | None, Query()] = None,
    scope: Annotated[str | None, Query()] = None,
) -> ToolListRead:
    """列出启用的工具;kind=function|interaction、scope 可过滤。

    带账号凭据(Bearer,或鉴权关闭时的 X-Account-Id)时并入该账号的连接器
    工具;不带凭据只返回内置目录(orchestrator 按账号带 Bearer 拉取,运维
    与健康检查仍可匿名读内置目录)。
    """

    tools = list(descriptors())
    if connector is not None and account_id is not None:
        tools.extend(await connector.tools(account_id))
    items = [
        tool
        for tool in tools
        if tool.enabled
        and is_available(tool.name, settings)
        and (kind is None or str(tool.kind) == kind)
        and (scope is None or scope in tool.scopes)
    ]
    return ToolListRead(
        tools=[ToolDescriptorRead.from_descriptor(tool) for tool in items],
        catalog_version=catalog_version(),
    )


@router.get("/{name}", response_model=ToolDescriptorRead)
async def get_tool(
    name: str,
    settings: SettingsDep,
    connector: ConnectorDep,
    account_id: OptionalAccountDep,
) -> ToolDescriptorRead:
    """按正式名或别名取单个工具描述(带账号凭据时可查连接器工具)。"""

    descriptor = find_descriptor(name)
    if descriptor is None and connector is not None and account_id is not None:
        descriptor = await connector.find(account_id, name)
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
    account_id: AccountDep,
    runner: RunnerDep,
    settings: SettingsDep,
    connector: ConnectorDep,
) -> JSONResponse:
    """执行工具;幂等键同参重放、异参 409。

    查重顺序:idempotency_key(调用方的稳定键,崩溃恢复后 tool_call_id 会变)
    优先,没有则回落 tool_call_id(旧客户端行为不变)。执行前依次做:单 run
    调用次数/写配额兜底、读前置校验(P1);这些拒绝也是模型可见的工具错误
    (HTTP 200 + ok=false),并照样落审计。
    """

    descriptor = find_descriptor(name)
    if descriptor is None and connector is not None:
        # 连接器工具不在静态目录里,按账号现查(TTL 缓存)。
        descriptor = await connector.find(account_id, name)
    if descriptor is None or not descriptor.enabled:
        raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
    # 能力开关与目录一致:关闭的工具(exec/fetch/连接器)连执行入口都不存在。
    if not is_available(descriptor.name, settings):
        raise NotFoundError("tool.not_found", f"工具 {name} 不存在或未启用")
    repository = ToolCallRepository(session)
    digest = arguments_digest(body.arguments)
    existing = await _find_existing(repository, account_id, body)
    if existing is not None and existing.status in TERMINAL_STATUSES:
        return _replay_response(existing, digest, body)
    # 防御性风险下限:WRITE/DESTRUCTIVE 没有 approved=true 一律 403。
    # orchestrator 只在审批策略自动放行或人工确认后才传 approved=true。
    # 放在抢占之前:抢占会重新持有租约,再 403 就留下一条无人释放的 running 行。
    if descriptor.risk in (ToolRisk.WRITE, ToolRisk.DESTRUCTIVE) and not body.approved:
        raise PermissionDeniedError(
            "tool.confirmation_required",
            f"工具 {descriptor.name} 需要人工确认后才能执行",
        )
    decision = "approved" if body.approved else "auto"
    lease_until = lease_deadline(settings.reservation_lease_seconds)
    record_id: str | None = None
    if existing is not None:
        # 只剩 running 一种可能(终态上面已重放):租约过期则抢占重试,
        # 没过期说明另一次调用正在执行,回 409 让调用方稍后按同键再试。
        if existing.arguments_sha256 != digest:
            raise _conflict(existing)
        if await takeover(session, existing.id, lease_until=lease_until):
            record_id = existing.id
        else:
            raise _in_progress(existing)
    context = ExecutionContext(
        account_id=account_id,
        tool_call_id=body.tool_call_id,
        run_id=body.run_id,
        conversation_id=body.conversation_id,
        workspace_id=body.workspace_id,
        sandbox_scope=body.sandbox_scope,
        timeout_seconds=body.timeout_seconds,
        approved=body.approved,
    )
    # 一次 tool_call 一个 span:目录/鉴权/幂等重放都在上面,不算执行本身。
    with tool_span(
        descriptor,
        context,
        arguments=body.arguments,
        log_content=settings.log_content,
    ) as span:
        outcome, internal_arguments = await _preflight(
            session, settings, descriptor, context, body.arguments
        )
        if outcome is not None:
            # 配额/读前置拒绝:没有执行,直接落终态行(不预留、不占租约)。
            columns = audit_columns(outcome, context, body.arguments, decision=decision)
            finish_tool_span(span, columns=columns, ok=False)
            replay = await _finish_without_execution(
                session,
                repository,
                record_id,
                account_id=account_id,
                body=body,
                descriptor=descriptor,
                decision=decision,
                columns=columns,
                status=STATUS_FAILED,
                digest=digest,
            )
            return replay or JSONResponse(outcome.response.envelope())
        if record_id is None:
            record_id, replay = await _reserve(
                session,
                repository,
                account_id=account_id,
                body=body,
                descriptor=descriptor,
                decision=decision,
                lease_until=lease_until,
                digest=digest,
            )
            if replay is not None:
                return replay
        try:
            outcome = await await_with_cancel(
                lambda: runner.execute(
                    context,
                    descriptor.name,
                    body.arguments,
                    internal_arguments=internal_arguments,
                ),
                probe=lambda: cancel_requested(session, record_id),
                poll_interval=settings.cancel_poll_seconds,
            )
            await _remember_read(session, descriptor, context, body, outcome)
            columns = audit_columns(outcome, context, body.arguments, decision=decision)
            finish_tool_span(span, columns=columns, ok=columns["ok"])
            status = STATUS_SUCCEEDED if outcome.response.ok else STATUS_FAILED
            await settle(
                session,
                record_id,
                status=status,
                columns=columns,
                result_json=_stored_result(outcome),
            )
        except ToolCallCancelled:
            # 取消是模型可见的工具错误(HTTP 200 + ok=false),不是 API 错误:
            # 模型需要知道"这次没跑完,别再等结果",而不是收到 409 去重试。
            outcome = failure_outcome(
                descriptor,
                context,
                CANCELLED_CODE,
                CANCELLED_MESSAGE,
                retryable=True,
            )
            columns = audit_columns(outcome, context, body.arguments, decision=decision)
            finish_tool_span(span, columns=columns, ok=False)
            await settle(session, record_id, status=STATUS_CANCELLED, columns=columns)
            return JSONResponse(outcome.response.envelope())
        except BaseException:
            # 执行抛异常(不是工具失败信封):先把预留行结掉,别让它挂到租约过期,
            # 否则同键重试会被 409 挡住;异常本身照旧冒泡(500)。
            columns = audit_columns(
                failure_outcome(
                    descriptor,
                    context,
                    "tool.internal_error",
                    "工具执行时发生内部错误",
                ),
                context,
                body.arguments,
                decision=decision,
            )
            await settle(session, record_id, status=STATUS_FAILED, columns=columns)
            raise
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
            context.account_id, run_id=context.run_id
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
                context.account_id, context.run_id
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
        context.account_id,
        sandbox_scope=context.sandbox_scope,
        conversation_id=context.conversation_id,
        run_id=context.run_id,
    )
    record = await FileReadRepository(session).find_read(
        context.account_id, scope_key=scope_key, path=path
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
        context.account_id,
        sandbox_scope=context.sandbox_scope,
        conversation_id=context.conversation_id,
        run_id=context.run_id,
    )
    await FileReadRepository(session).upsert_read(
        context.account_id,
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


async def _find_existing(
    repository: ToolCallRepository, account_id: str, body: ToolExecuteBody
) -> ToolCall | None:
    """按幂等键找已执行记录:稳定键优先,再回落 tool_call_id。"""

    if body.idempotency_key:
        existing = await repository.find_by_idempotency_key(
            account_id, body.idempotency_key
        )
        if existing is not None:
            return existing
    return await repository.find_by_call_id(account_id, body.tool_call_id)


async def _reserve(
    session: AsyncSession,
    repository: ToolCallRepository,
    *,
    account_id: str,
    body: ToolExecuteBody,
    descriptor: ToolDescriptor,
    decision: str,
    lease_until: datetime,
    digest: str,
) -> tuple[str, JSONResponse | None]:
    """插一条 running 预留行并提交;并发同键时回滚成重放/409。

    提交后才开始执行:唯一约束现在由「预留」承担,同键第二次请求会在
    执行开始前就 409 tool.call_in_progress,而不是执行完再撞约束。
    """

    record = ToolCall(
        account_id=account_id,
        tool_call_id=body.tool_call_id,
        idempotency_key=body.idempotency_key,
        run_id=body.run_id,
        conversation_id=body.conversation_id,
        status=STATUS_RUNNING,
        lease_until=lease_until,
        cancel_requested=False,
        **reservation_columns(descriptor, body.arguments, decision=decision),
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_existing(repository, account_id, body)
        if existing is None:
            raise
        if existing.status in TERMINAL_STATUSES:
            return "", _replay_response(existing, digest, body)
        if existing.arguments_sha256 != digest:
            raise _conflict(existing) from None
        raise _in_progress(existing) from None
    return str(record.id), None


async def _finish_without_execution(
    session: AsyncSession,
    repository: ToolCallRepository,
    record_id: str | None,
    *,
    account_id: str,
    body: ToolExecuteBody,
    descriptor: ToolDescriptor,
    decision: str,
    columns: dict[str, Any],
    status: str,
    digest: str,
) -> JSONResponse | None:
    """执行前就被拒绝(配额/读前置):结掉已抢到的预留行,或直接落终态行。

    返回非 None 表示并发请求先落了记录,应按它重放。
    """

    if record_id is not None:
        await settle(session, record_id, status=status, columns=columns)
        return None
    record = ToolCall(
        account_id=account_id,
        tool_call_id=body.tool_call_id,
        idempotency_key=body.idempotency_key,
        run_id=body.run_id,
        conversation_id=body.conversation_id,
        status=status,
        **columns,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _find_existing(repository, account_id, body)
        if existing is None:
            raise
        return _replay_response(existing, digest, body)
    return None


def _conflict(existing: ToolCall) -> ConflictError:
    """同键异参:409 idempotency_conflict(与首次记录对照)。"""

    return ConflictError(
        "tool.idempotency_conflict",
        "同一幂等键的参数与首次调用不一致",
        details={
            "tool_call_id": existing.tool_call_id,
            "idempotency_key": existing.idempotency_key,
        },
    )


def _in_progress(existing: ToolCall) -> ConflictError:
    """同键仍在执行:409,带租约到期时间让调用方知道要等多久。"""

    lease = existing.lease_until
    return ConflictError(
        IN_PROGRESS_CODE,
        IN_PROGRESS_MESSAGE,
        details={
            "tool_call_id": existing.tool_call_id,
            "idempotency_key": existing.idempotency_key,
            "status": existing.status,
            "lease_until": lease.isoformat() if lease is not None else None,
        },
    )


def _stored_result(outcome: ExecutionOutcome) -> str | None:
    """成功调用的结果快照(重放时回原结果而不是空壳);失败不留结果。"""

    result = outcome.response.result
    if not outcome.response.ok or not result:
        return None
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - 结果本就来自 JSON
        return None


def _replay_response(
    existing: ToolCall, digest: str, body: ToolExecuteBody
) -> JSONResponse:
    """同一逻辑调用的第二次请求:参数一致则原样重放,否则 409。

    有结果快照(result_json,2026-11-17 起)时回真实结果,让重放对模型可读;
    老记录没有快照,退回「已执行,未重复执行」的说明。
    """

    if existing.arguments_sha256 != digest:
        raise _conflict(existing)
    error: ToolError | None = None
    result: dict[str, Any] | None = None
    # 稳定键命中时 tool_call_id 可能已换(崩溃恢复后模型重新生成),提示按
    # 逻辑调用而不是按 id 讲,避免用户以为"另一个调用被去重"。
    label = (
        "该逻辑调用"
        if body.idempotency_key and body.idempotency_key == existing.idempotency_key
        else "该 tool_call_id"
    )
    stored = _stored_result_of(existing)
    if existing.status == STATUS_CANCELLED:
        error = ToolError(
            code=CANCELLED_CODE,
            message=f"{label} 已被取消,本次未执行",
            retryable=True,
        )
    elif existing.ok:
        result = {
            **(stored or {}),
            "replayed": True,
            "note": f"{label} 已执行成功,本次未重复执行",
        }
    else:
        error = ToolError(
            code=existing.error_code or "tool.replayed",
            message=f"{label} 已执行失败,本次未重复执行",
            retryable=False,
        )
    response = ToolExecuteResponse(
        ok=existing.status != STATUS_CANCELLED and existing.ok,
        result=result,
        error=error,
        truncated=existing.truncated,
        duration_ms=existing.duration_ms,
        tool_call_id=existing.tool_call_id,
        idempotent_replay=True,
    )
    return JSONResponse(response.envelope())


def _stored_result_of(existing: ToolCall) -> dict[str, Any] | None:
    """读回结果快照;解析失败按没有快照处理(重放文案仍可用)。"""

    raw = existing.result_json
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:  # pragma: no cover - 只有写坏的行才会触发
        return None
    return value if isinstance(value, dict) else None
