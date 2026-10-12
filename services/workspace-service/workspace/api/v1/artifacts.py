"""交付物端点:登记/列表/读取/删除(§7)。"""

from __future__ import annotations

from typing import Annotated

from common.artifacts import ArtifactStore
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.api.deps import (
    get_artifact_store,
    get_session,
    get_settings,
    get_tenant_id,
)
from workspace.core.config import Settings
from workspace.schemas.workspace import (
    ArtifactCreate,
    ArtifactMeta,
    ArtifactRead,
)
from workspace.services import artifacts as artifacts_service

router = APIRouter(tags=["artifacts"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]


@router.post(
    "/artifacts", response_model=ArtifactRead, status_code=status.HTTP_201_CREATED
)
async def create_artifact(
    body: ArtifactCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    store: StoreDep,
) -> ArtifactRead:
    """登记交付物:不传 workspace_id 则落默认工作区。"""
    record = await artifacts_service.create_artifact(
        session, tenant_id, body, store, settings.default_workspace_name
    )
    content = await artifacts_service.load_content(record, store)
    return _to_read(record, content)


@router.get("/workspaces/{workspace_id}/artifacts", response_model=list[ArtifactMeta])
async def list_artifacts(
    workspace_id: str, session: DBSession, tenant_id: TenantId
) -> list[ArtifactMeta]:
    """工作区内的交付物元数据(倒序,不含正文)。"""
    records = await artifacts_service.list_artifacts(session, tenant_id, workspace_id)
    return [ArtifactMeta.model_validate(record) for record in records]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactRead)
async def get_artifact(
    artifact_id: str, session: DBSession, tenant_id: TenantId, store: StoreDep
) -> ArtifactRead:
    """读交付物:元数据 + 正文(按存储后端装载)。"""
    record = await artifacts_service.require_artifact(session, tenant_id, artifact_id)
    content = await artifacts_service.load_content(record, store)
    return _to_read(record, content)


@router.delete("/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_artifact(
    artifact_id: str, session: DBSession, tenant_id: TenantId, store: StoreDep
) -> Response:
    """删除交付物:对象与元数据一起清理。"""
    await artifacts_service.delete_artifact(session, tenant_id, artifact_id, store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _to_read(record, content: str) -> ArtifactRead:
    return ArtifactRead(
        id=record.id,
        workspace_id=record.workspace_id,
        run_id=record.run_id,
        kind=record.kind,
        title=record.title,
        storage=record.storage,
        size_bytes=record.size_bytes,
        created_at=record.created_at,
        content=content,
    )
