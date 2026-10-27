"""沙箱端点:创建/幂等 ensure/查询/列表/执行/文件读写/目录树/续期/删除。

(§7 阶段 B / P1)
"""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.api.deps import (
    get_sandbox_provider,
    get_session,
    get_settings,
    get_tenant_id,
)
from workspace.core.config import Settings
from workspace.schemas.sandbox import (
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
    SandboxTreeRead,
)
from workspace.services import sandbox as sandbox_service
from workspace.services.sandbox_provider import SandboxProvider

router = APIRouter(prefix="/sandboxes", tags=["sandboxes"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
ProviderDep = Annotated[SandboxProvider, Depends(get_sandbox_provider)]

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
    sandbox_id: str, session: DBSession, tenant_id: TenantId, provider: ProviderDep
) -> Response:
    """删除沙箱:先销毁运行时,再删记录。"""
    await sandbox_service.destroy_sandbox(session, tenant_id, sandbox_id, provider)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
