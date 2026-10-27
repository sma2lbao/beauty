"""沙箱端点:创建/幂等 ensure/查询/列表/执行/文件读写/目录树/续期/删除。

外加持久 shell 会话(#2):开会话 / 写输入 / SSE 输出流(断线回放) / 关闭。

(§7 阶段 B / P1)
"""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from typing import Annotated

from common.errors import AppError, NotFoundError
from database.session import session_scope
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.api.deps import (
    get_sandbox_provider,
    get_session,
    get_settings,
    get_shell_sessions,
    get_tenant_id,
)
from workspace.core.config import Settings
from workspace.models.sandbox import SandboxInstance
from workspace.schemas.sandbox import (
    DEFAULT_CWD,
    MAX_PATH_LENGTH,
    MAX_SCOPE_KEY_LENGTH,
    MAX_TREE_DEPTH,
    WORKSPACE_ROOT,
    SandboxCreate,
    SandboxEnsure,
    SandboxEnsureRead,
    SandboxExecRead,
    SandboxExecRequest,
    SandboxFileEntry,
    SandboxFileKind,
    SandboxFileRead,
    SandboxFileWrite,
    SandboxRead,
    SandboxSessionCreate,
    SandboxSessionInput,
    SandboxSessionRead,
    SandboxTreeRead,
)
from workspace.services import sandbox as sandbox_service
from workspace.services.sandbox_provider import SandboxProvider
from workspace.services.shell_sessions import ShellSessionManager, iter_sse_frames

router = APIRouter(prefix="/sandboxes", tags=["sandboxes"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ProviderDep = Annotated[SandboxProvider, Depends(get_sandbox_provider)]
ShellSessionsDep = Annotated[ShellSessionManager, Depends(get_shell_sessions)]

#: 沙箱已不可用(过期/停止/失败)时,其内存里的 shell 会话一并关闭再抛错。
_UNUSABLE_CODES = frozenset({"sandbox.expired", "sandbox.stopped", "sandbox.failed"})

_TREE_KINDS: dict[str, SandboxFileKind] = {
    "file": "file",
    "dir": "dir",
    "symlink": "symlink",
    "other": "other",
}


def _entry_of(
    path: str, kind: str, size: int, modified: float | None
) -> SandboxFileEntry:
    """provider 的 FileEntry → 契约条目;name 取路径尾段,未知 kind 归 other。"""
    return SandboxFileEntry(
        path=path,
        name=PurePosixPath(path).name,
        kind=_TREE_KINDS.get(kind, "other"),
        size_bytes=size,
        modified=modified,
    )


async def _usable_sandbox(
    session: AsyncSession,
    tenant_id: str,
    sandbox_id: str,
    provider: SandboxProvider,
    settings: Settings,
    shell_sessions: ShellSessionManager,
) -> SandboxInstance:
    """取可用沙箱并续期;不可用时先关掉它残留的 shell 会话,再抛原错误。"""
    try:
        record = await sandbox_service.require_usable_sandbox(
            session, tenant_id, sandbox_id, provider
        )
    except AppError as exc:
        if exc.code in _UNUSABLE_CODES:
            # 过期/停止/失败:运行时已经没了,内存里的会话只会等不到 EOF
            await shell_sessions.close_sandbox(tenant_id, sandbox_id)
        raise
    sandbox_service.renew_sandbox(record, settings)
    return record


def _parse_last_event_id(value: str | None) -> int | None:
    """SSE 断线续传游标:Last-Event-ID 是上一次收到的事件 seq(非负整数)。"""
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        cursor = int(text)
    except ValueError as exc:
        raise AppError(
            "sandbox.bad_cursor",
            "Last-Event-ID 必须是整数",
            details={"last_event_id": text[:64]},
        ) from exc
    if cursor < 0:
        raise AppError(
            "sandbox.bad_cursor",
            "Last-Event-ID 不能为负数",
            details={"last_event_id": text[:64]},
        )
    return cursor


_SSE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.post("", response_model=SandboxRead, status_code=status.HTTP_201_CREATED)
async def create_sandbox(
    body: SandboxCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
) -> SandboxRead:
    """创建沙箱:先落库(creating),再拉起运行时;失败保留 failed 记录。"""
    record = await sandbox_service.create_sandbox(
        session, tenant_id, body, provider, settings
    )
    return SandboxRead.model_validate(record)


@router.post(":ensure", response_model=SandboxEnsureRead)
async def ensure_sandbox(
    body: SandboxEnsure,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
    response: Response,
) -> SandboxEnsureRead:
    """幂等 get-or-create:同 (tenant_id, scope_key) 只有一个沙箱。

    新建运行时返回 201,复用现有沙箱返回 200(响应体结构一致)。
    """
    record, created = await sandbox_service.ensure_sandbox(
        session,
        tenant_id,
        scope_key=body.scope_key,
        spec=body,
        provider=provider,
        settings=settings,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return SandboxEnsureRead(
        sandbox=SandboxRead.model_validate(record), created=created
    )


@router.get("", response_model=list[SandboxRead])
async def list_sandboxes(
    session: DBSession,
    tenant_id: TenantId,
    scope_key: Annotated[
        str | None, Query(min_length=1, max_length=MAX_SCOPE_KEY_LENGTH)
    ] = None,
) -> list[SandboxRead]:
    """沙箱列表(仅本租户,新建在前);scope_key 精确过滤到某个会话/run。"""
    records = await sandbox_service.list_sandboxes(
        session, tenant_id, scope_key=scope_key
    )
    return [SandboxRead.model_validate(record) for record in records]


@router.get("/{sandbox_id}", response_model=SandboxRead)
async def get_sandbox(
    sandbox_id: str, session: DBSession, tenant_id: TenantId
) -> SandboxRead:
    """沙箱详情(仅本租户可见,跨租户一律 404)。"""
    record = await sandbox_service.require_sandbox(session, tenant_id, sandbox_id)
    return SandboxRead.model_validate(record)


@router.post("/{sandbox_id}/exec", response_model=SandboxExecRead)
async def exec_sandbox(
    sandbox_id: str,
    body: SandboxExecRequest,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
) -> SandboxExecRead:
    """在沙箱内执行命令;成功后滑动续期。"""
    result = await sandbox_service.exec_sandbox(
        session, tenant_id, sandbox_id, body, provider, settings
    )
    return SandboxExecRead(
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        truncated=result.truncated,
    )


@router.put("/{sandbox_id}/files", status_code=status.HTTP_204_NO_CONTENT)
async def write_sandbox_file(
    sandbox_id: str,
    body: SandboxFileWrite,
    session: DBSession,
    tenant_id: TenantId,
    provider: ProviderDep,
) -> Response:
    """写文件:路径必须落在 /workspace 下,内容用 Base64。"""
    await sandbox_service.write_file(session, tenant_id, sandbox_id, body, provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{sandbox_id}/files", response_model=SandboxFileRead)
async def read_sandbox_file(
    sandbox_id: str,
    path: Annotated[str, Query(min_length=1, max_length=MAX_PATH_LENGTH)],
    session: DBSession,
    tenant_id: TenantId,
    provider: ProviderDep,
) -> SandboxFileRead:
    """读文件:返回 Base64 内容与解码后的字节数。"""
    data = await sandbox_service.read_file(
        session, tenant_id, sandbox_id, path, provider
    )
    return SandboxFileRead(
        path=path,
        content_base64=base64.b64encode(data).decode("ascii"),
        size_bytes=len(data),
    )


@router.get("/{sandbox_id}/tree", response_model=SandboxTreeRead)
async def list_sandbox_tree(
    sandbox_id: str,
    session: DBSession,
    tenant_id: TenantId,
    provider: ProviderDep,
    path: Annotated[
        str, Query(min_length=1, max_length=MAX_PATH_LENGTH)
    ] = WORKSPACE_ROOT,
    depth: Annotated[int, Query(ge=1, le=MAX_TREE_DEPTH)] = 1,
) -> SandboxTreeRead:
    """列沙箱目录树:/workspace 下的扁平条目列表,超上限时 truncated=true。"""
    entries, truncated = await sandbox_service.list_tree(
        session, tenant_id, sandbox_id, path, depth, provider
    )
    return SandboxTreeRead(
        path=path,
        entries=[
            _entry_of(item.path, item.kind, item.size, item.modified)
            for item in entries
        ],
        truncated=truncated,
    )


@router.post("/{sandbox_id}/touch", response_model=SandboxRead)
async def touch_sandbox(
    sandbox_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
) -> SandboxRead:
    """显式续期(滑动 TTL,上限 sandbox_max_ttl_seconds)。"""
    record = await sandbox_service.touch_sandbox(
        session, tenant_id, sandbox_id, provider, settings
    )
    return SandboxRead.model_validate(record)


@router.delete("/{sandbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sandbox(
    sandbox_id: str,
    session: DBSession,
    tenant_id: TenantId,
    provider: ProviderDep,
    shell_sessions: ShellSessionsDep,
) -> Response:
    """删除沙箱:先销毁运行时,再删记录,并关闭其全部 shell 会话。"""
    try:
        await sandbox_service.destroy_sandbox(session, tenant_id, sandbox_id, provider)
    except NotFoundError:
        await shell_sessions.close_sandbox(tenant_id, sandbox_id)
        raise
    await shell_sessions.close_sandbox(tenant_id, sandbox_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{sandbox_id}/sessions",
    response_model=SandboxSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_sandbox_session(
    sandbox_id: str,
    body: SandboxSessionCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
    shell_sessions: ShellSessionsDep,
) -> SandboxSessionRead:
    """开持久 shell 会话:microsandbox 走真 TTY,local 走 POSIX pty。

    会话活到显式 DELETE / 空闲超时 / 沙箱被销毁——不随 HTTP 请求结束而结束。
    """
    record = await _usable_sandbox(
        session, tenant_id, sandbox_id, provider, settings, shell_sessions
    )
    info = await shell_sessions.open(
        tenant_id=tenant_id,
        sandbox_id=sandbox_id,
        runtime_name=record.external_name,
        provider=provider,
        cwd=body.cwd or DEFAULT_CWD,
        cols=body.cols,
        rows=body.rows,
    )
    return SandboxSessionRead(
        session_id=info.session_id,
        sandbox_id=info.sandbox_id,
        cwd=info.cwd,
        created_at=info.created_at,
    )


@router.post(
    "/{sandbox_id}/sessions/{session_id}/input",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def write_sandbox_session_input(
    sandbox_id: str,
    session_id: str,
    body: SandboxSessionInput,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
    shell_sessions: ShellSessionsDep,
) -> Response:
    """向会话 stdin 写文本(UTF-8 编码,单次上限 8KB)。"""
    await _usable_sandbox(
        session, tenant_id, sandbox_id, provider, settings, shell_sessions
    )
    await shell_sessions.write(
        tenant_id=tenant_id,
        sandbox_id=sandbox_id,
        session_id=session_id,
        data=body.data.encode("utf-8"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{sandbox_id}/sessions/{session_id}/stream")
async def stream_sandbox_session(
    request: Request,
    sandbox_id: str,
    session_id: str,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
    shell_sessions: ShellSessionsDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE 输出流:先回放环形缓冲(支持 Last-Event-ID 续传),再跟随实时输出。

    这里刻意不用 DBSession 依赖:yield 依赖在 StreamingResponse 期间一直存活,
    长连接会把一个数据库连接/事务挂 30 分钟以上。改成先用短事务校验沙箱,
    校验完立刻归还连接,再开始流式输出。
    """
    cursor = _parse_last_event_id(last_event_id)
    factory = request.app.state.session_factory
    async with session_scope(factory) as db:
        await _usable_sandbox(
            db, tenant_id, sandbox_id, provider, settings, shell_sessions
        )
    backlog, queue = shell_sessions.subscribe(
        tenant_id=tenant_id,
        sandbox_id=sandbox_id,
        session_id=session_id,
        after=cursor,
    )
    frames = iter_sse_frames(
        backlog,
        queue,
        heartbeat_seconds=settings.sandbox_shell_heartbeat_seconds,
        on_close=lambda: shell_sessions.unsubscribe(
            tenant_id=tenant_id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            queue=queue,
        ),
    )
    return StreamingResponse(
        frames, media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.delete(
    "/{sandbox_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_sandbox_session(
    sandbox_id: str,
    session_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    provider: ProviderDep,
    shell_sessions: ShellSessionsDep,
) -> Response:
    """关闭会话(幂等):未知会话 404,重复关闭仍 204。"""
    await _usable_sandbox(
        session, tenant_id, sandbox_id, provider, settings, shell_sessions
    )
    await shell_sessions.close(
        tenant_id=tenant_id, sandbox_id=sandbox_id, session_id=session_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
