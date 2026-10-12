"""交付物业务逻辑:内容经 common.artifacts 落存储,DB 只留元数据(§7)。"""

from __future__ import annotations

from uuid import uuid4

from common.artifacts import (
    ArtifactStore,
    build_artifact_key,
)
from common.errors import NotFoundError, StorageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.workspace import WorkspaceArtifact
from workspace.schemas.workspace import ArtifactCreate
from workspace.services import workspaces as workspaces_service

CONTENT_TYPE = "text/markdown"
SUFFIX = ".md"


async def create_artifact(
    session: AsyncSession,
    tenant_id: str,
    data: ArtifactCreate,
    store: ArtifactStore | None,
    default_workspace_name: str,
) -> WorkspaceArtifact:
    """登记交付物:正文落对象存储(或 DB 列),行里留元数据与引用。"""
    workspace = (
        await workspaces_service.require_workspace(
            session, tenant_id, data.workspace_id
        )
        if data.workspace_id
        else await workspaces_service.require_or_create_default(
            session, tenant_id, default_workspace_name
        )
    )
    payload = data.content.encode("utf-8")
    object_id = uuid4().hex
    record = WorkspaceArtifact(
        id=object_id,
        tenant_id=tenant_id,
        workspace_id=workspace.id,
        run_id=data.run_id,
        kind=data.kind,
        title=data.title,
        storage="filesystem" if store is not None else "database",
        size_bytes=len(payload),
        content=None if store is not None else data.content,
    )
    if store is not None:
        key = build_artifact_key(tenant_id, workspace.id, object_id, suffix=SUFFIX)
        await store.put(key, payload, content_type=CONTENT_TYPE)
        record.object_key = key
    session.add(record)
    await session.flush()
    return record


async def require_artifact(
    session: AsyncSession, tenant_id: str, artifact_id: str
) -> WorkspaceArtifact:
    record = await session.scalar(
        select(WorkspaceArtifact).where(
            WorkspaceArtifact.id == artifact_id,
            WorkspaceArtifact.tenant_id == tenant_id,
        )
    )
    if record is None:
        raise NotFoundError(
            "artifact.not_found",
            "交付物不存在",
            details={"artifact_id": artifact_id},
        )
    return record


async def load_content(record: WorkspaceArtifact, store: ArtifactStore | None) -> str:
    """装载正文:有 object_key 走存储,否则读 DB 列。"""
    if not record.object_key:
        return record.content or ""
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
    return data.decode("utf-8", "replace")


async def list_artifacts(
    session: AsyncSession, tenant_id: str, workspace_id: str
) -> list[WorkspaceArtifact]:
    await workspaces_service.require_workspace(session, tenant_id, workspace_id)
    rows = await session.scalars(
        select(WorkspaceArtifact)
        .where(
            WorkspaceArtifact.tenant_id == tenant_id,
            WorkspaceArtifact.workspace_id == workspace_id,
        )
        .order_by(WorkspaceArtifact.created_at.desc(), WorkspaceArtifact.id.desc())
    )
    return list(rows)


async def delete_artifact(
    session: AsyncSession,
    tenant_id: str,
    artifact_id: str,
    store: ArtifactStore | None,
) -> None:
    """删除交付物:先删对象再删元数据(对象缺失不阻塞删除)。"""
    record = await require_artifact(session, tenant_id, artifact_id)
    if record.object_key and store is not None:
        await store.delete(record.object_key)
    await session.delete(record)
    await session.flush()
