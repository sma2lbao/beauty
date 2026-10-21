"""交付物业务逻辑:内容经 common.artifacts 落存储,DB 只留元数据(§7)。

数据访问经 `workspace.repositories`;本层负责存储与元数据的编排。

交付物支持二进制:调用方用 `content_base64` 传字节(文本仍走 `content`),
本层把两种入参统一归一成 bytes 后落同一套存储;行里另记 filename/content_type,
供下载端点使用。database 后端按文本与二进制分列存放(content / content_bytes),
filesystem 后端两者都进对象存储,行里只留 object_key。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from uuid import uuid4

from common.artifacts import ArtifactStore, build_artifact_key
from common.errors import StorageError
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.workspace import WorkspaceArtifact
from workspace.repositories import ArtifactRepository
from workspace.schemas.workspace import ArtifactCreate
from workspace.services import workspaces as workspaces_service

TEXT_CONTENT_TYPE = "text/markdown"
BINARY_CONTENT_TYPE = "application/octet-stream"
TEXT_SUFFIX = ".md"
BINARY_SUFFIX = ".bin"

# content_type → 对象键后缀(对象键只允许 [A-Za-z0-9._-])
_SUFFIX_BY_CONTENT_TYPE = {
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/json": ".json",
    "application/xml": ".xml",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/octet-stream": ".bin",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
}
_STRUCTURED_TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/javascript",
}
_SAFE_EXT = re.compile(r"^\.[A-Za-z0-9_-]{1,16}$")


def is_text_content_type(content_type: str | None) -> bool:
    """是否按文本处理:无标注(老数据)按文本;text/* 与常见结构化文本也算文本。"""
    if not content_type:
        return True
    main = content_type.split(";", 1)[0].strip().lower()
    return main.startswith("text/") or main in _STRUCTURED_TEXT_TYPES


def artifact_payload(data: ArtifactCreate) -> bytes:
    """契约层已校验 base64,这里再解码一次拿到字节(文本按 UTF-8 编码)。"""
    if data.content_base64 is not None:
        return base64.b64decode(data.content_base64)
    return (data.content or "").encode("utf-8")


def resolve_metadata(data: ArtifactCreate, *, is_binary: bool) -> tuple[str, str]:
    """归一 content_type 与 filename:显式值优先,否则按文本/二进制给默认值。"""
    content_type = data.content_type or (
        BINARY_CONTENT_TYPE if is_binary else TEXT_CONTENT_TYPE
    )
    filename = data.filename or f"{data.title}{_suffix_for(content_type, is_binary)}"
    return content_type, filename


def _suffix_for(content_type: str | None, is_binary: bool) -> str:
    """content_type 对应的安全后缀;未知类型退回 .bin / .md。"""
    if content_type:
        suffix = _SUFFIX_BY_CONTENT_TYPE.get(
            content_type.split(";", 1)[0].strip().lower()
        )
        if suffix:
            return suffix
    return BINARY_SUFFIX if is_binary else TEXT_SUFFIX


def artifact_suffix(filename: str, content_type: str | None, *, is_binary: bool) -> str:
    """对象键后缀:优先取 filename 的扩展名,其次按 content_type 推。"""
    ext = Path(filename).suffix
    if _SAFE_EXT.match(ext):
        return ext
    return _suffix_for(content_type, is_binary)


def artifact_filename(record: WorkspaceArtifact) -> str:
    """下载文件名:行里有 filename 用它,否则由 title + 类型后缀兜底(老数据)。"""
    if record.filename:
        return record.filename
    is_binary = not is_text_content_type(record.content_type)
    return f"{record.title}{_suffix_for(record.content_type, is_binary)}"


async def create_artifact(
    session: AsyncSession,
    tenant_id: str,
    data: ArtifactCreate,
    store: ArtifactStore | None,
    default_workspace_name: str,
) -> WorkspaceArtifact:
    """登记交付物:字节落对象存储(或 DB 列),行里留元数据与引用。"""
    workspace = (
        await workspaces_service.require_workspace(
            session, tenant_id, data.workspace_id
        )
        if data.workspace_id
        else await workspaces_service.require_or_create_default(
            session, tenant_id, default_workspace_name
        )
    )
    is_binary = data.content_base64 is not None
    payload = artifact_payload(data)
    content_type, filename = resolve_metadata(data, is_binary=is_binary)
    object_id = uuid4().hex
    record = WorkspaceArtifact(
        id=object_id,
        tenant_id=tenant_id,
        workspace_id=workspace.id,
        run_id=data.run_id,
        kind=data.kind,
        title=data.title,
        filename=filename,
        content_type=content_type,
        storage="filesystem" if store is not None else "database",
        size_bytes=len(payload),
        # database 后端:文本进 content,二进制进 content_bytes
        content=None if store is not None or is_binary else data.content,
        content_bytes=None if store is not None or not is_binary else payload,
    )
    if store is not None:
        key = build_artifact_key(
            tenant_id,
            workspace.id,
            object_id,
            suffix=artifact_suffix(filename, content_type, is_binary=is_binary),
        )
        await store.put(key, payload, content_type=content_type)
        record.object_key = key
    return await ArtifactRepository(session).add(record)


async def require_artifact(
    session: AsyncSession, tenant_id: str, artifact_id: str
) -> WorkspaceArtifact:
    return await ArtifactRepository(session).require_for_tenant(tenant_id, artifact_id)


async def load_bytes(record: WorkspaceArtifact, store: ArtifactStore | None) -> bytes:
    """装载原始字节:content_bytes(DB 二进制)→ object_key(存储)→ content(文本)。"""
    if record.content_bytes is not None:
        return bytes(record.content_bytes)
    if not record.object_key:
        return (record.content or "").encode("utf-8")
    if store is None:
        raise StorageError(
            "artifact.store_missing",
            "交付物在对象存储中,但当前未配置存储后端",
            details={"object_key": record.object_key},
        )
    data = await store.get(record.object_key)
    if data is None:
        raise StorageError(
            "artifact.object_missing",
            f"交付物对象不存在:{record.object_key}",
            details={"object_key": record.object_key},
        )
    return data


async def load_content(
    record: WorkspaceArtifact, store: ArtifactStore | None
) -> str | None:
    """装载正文文本;二进制交付物返回 None(与 ArtifactRead.content 语义一致)。

    二进制判定看 content_type;历史数据没有 content_type,按文本处理。
    """
    if not is_text_content_type(record.content_type):
        return None
    if record.content_bytes is not None:
        return bytes(record.content_bytes).decode("utf-8", "replace")
    if not record.object_key:
        return record.content or ""
    return (await load_bytes(record, store)).decode("utf-8", "replace")


async def list_artifacts(
    session: AsyncSession, tenant_id: str, workspace_id: str
) -> list[WorkspaceArtifact]:
    await workspaces_service.require_workspace(session, tenant_id, workspace_id)
    return await ArtifactRepository(session).list_scoped(
        tenant_id,
        WorkspaceArtifact.workspace_id == workspace_id,
        order_by=[WorkspaceArtifact.created_at.desc(), WorkspaceArtifact.id.desc()],
    )


async def delete_artifact(
    session: AsyncSession,
    tenant_id: str,
    artifact_id: str,
    store: ArtifactStore | None,
) -> None:
    """删除交付物:先删对象再删元数据(对象缺失不阻塞删除)。"""
    artifacts = ArtifactRepository(session)
    record = await artifacts.require_for_tenant(tenant_id, artifact_id)
    if record.object_key and store is not None:
        await store.delete(record.object_key)
    await artifacts.delete(record)
