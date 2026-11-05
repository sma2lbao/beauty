"""工作区端点:创建/列表/详情/改名/删除/默认工作区。"""

from __future__ import annotations

from typing import Annotated

from common.artifacts import ArtifactStore
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.api.deps import (
    get_account_id,
    get_artifact_store,
    get_session,
    get_settings,
)
from workspace.core.config import Settings
from workspace.schemas.workspace import WorkspaceCreate, WorkspaceRead, WorkspaceUpdate
from workspace.services import workspaces as workspaces_service

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate, session: DBSession, account_id: AccountId
) -> WorkspaceRead:
    """创建工作区(同账号内名称唯一,重名 409)。"""
    workspace = await workspaces_service.create_workspace(session, account_id, body)
    return WorkspaceRead.model_validate(workspace)


@router.get("", response_model=list[WorkspaceRead])
async def list_workspaces(
    session: DBSession, account_id: AccountId
) -> list[WorkspaceRead]:
    workspaces = await workspaces_service.list_workspaces(session, account_id)
    return [WorkspaceRead.model_validate(workspace) for workspace in workspaces]


@router.get("/default", response_model=WorkspaceRead)
async def get_default_workspace(
    session: DBSession, account_id: AccountId, settings: SettingsDep
) -> WorkspaceRead:
    """账号默认工作区(幂等建档):agent-orchestrator 交付时的落点。"""
    workspace = await workspaces_service.require_or_create_default(
        session, account_id, settings.default_workspace_name
    )
    return WorkspaceRead.model_validate(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace(
    workspace_id: str, session: DBSession, account_id: AccountId
) -> WorkspaceRead:
    workspace = await workspaces_service.require_workspace(
        session, account_id, workspace_id
    )
    return WorkspaceRead.model_validate(workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: str,
    body: WorkspaceUpdate,
    session: DBSession,
    account_id: AccountId,
) -> WorkspaceRead:
    """改名/改描述:未传字段不改,description 显式 null 清空,重名 409。

    默认工作区可以改名(改名后仍靠 `is_default` 识别),但不能删除。
    """
    workspace = await workspaces_service.update_workspace(
        session, account_id, workspace_id, body
    )
    return WorkspaceRead.model_validate(workspace)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    session: DBSession,
    account_id: AccountId,
    store: StoreDep,
) -> Response:
    """删除工作区:级联清理其交付物(对象 + 行);默认工作区 409。"""
    await workspaces_service.delete_workspace(session, account_id, workspace_id, store)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
