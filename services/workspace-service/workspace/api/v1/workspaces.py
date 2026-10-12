"""工作区端点:创建/列表/详情/默认工作区。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.api.deps import get_session, get_settings, get_tenant_id
from workspace.core.config import Settings
from workspace.schemas.workspace import WorkspaceCreate, WorkspaceRead
from workspace.services import workspaces as workspaces_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, session: DBSession, tenant_id: TenantId
) -> WorkspaceRead:
    """创建工作区(同租户内名称唯一,重名 409)。"""
    workspace = await workspaces_service.create_workspace(session, tenant_id, body)
    return WorkspaceRead.model_validate(workspace)


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: DBSession, tenant_id: TenantId
) -> list[WorkspaceRead]:
    workspaces = await workspaces_service.list_workspaces(session, tenant_id)
    return [WorkspaceRead.model_validate(workspace) for workspace in workspaces]


@router.get("/default", response_model=WorkspaceRead)
async def get_default_workspace(
    session: DBSession, tenant_id: TenantId, settings: SettingsDep
) -> WorkspaceRead:
    """租户默认工作区(幂等建档):agent-orchestrator 交付时的落点。"""
    workspace = await workspaces_service.require_or_create_default(
        session, tenant_id, settings.default_workspace_name
    )
    return WorkspaceRead.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_id: str, session: DBSession, tenant_id: TenantId
) -> WorkspaceRead:
    workspace = await workspaces_service.require_workspace(
        session, tenant_id, workspace_id
    )
    return WorkspaceRead.model_validate(workspace)
