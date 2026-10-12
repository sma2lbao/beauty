"""工作区业务逻辑:幂等默认工作区、创建/列表/详情。

数据访问经 `workspace.repositories`;本层只写业务规则,不手写租户过滤。
"""

from __future__ import annotations

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.workspace import Workspace
from workspace.repositories import WorkspaceRepository
from workspace.schemas.workspace import WorkspaceCreate


async def require_or_create_default(
    session: AsyncSession, tenant_id: str, default_name: str
) -> Workspace:
    """取租户的默认工作区;没有就建(幂等,供交付与首屏使用)。"""
    workspaces = WorkspaceRepository(session)
    found = await workspaces.list_scoped(tenant_id, Workspace.name == default_name)
    if found:
        return found[0]
    workspace = Workspace(
        tenant_id=tenant_id,
        name=default_name,
        description="自动创建:租户默认工作区",
        status="active",
    )
    return await workspaces.add(workspace)


async def create_workspace(
    session: AsyncSession, tenant_id: str, data: WorkspaceCreate
) -> Workspace:
    workspaces = WorkspaceRepository(session)
    duplicate = await workspaces.list_scoped(tenant_id, Workspace.name == data.name)
    if duplicate:
        raise ConflictError(
            "workspace.exists",
            "同名工作区已存在",
            details={"workspace_id": duplicate[0].id, "name": duplicate[0].name},
        )
    workspace = Workspace(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        status="active",
    )
    return await workspaces.add(workspace)


async def list_workspaces(session: AsyncSession, tenant_id: str) -> list[Workspace]:
    return await WorkspaceRepository(session).list_scoped(
        tenant_id, order_by=[Workspace.created_at, Workspace.id]
    )


async def require_workspace(
    session: AsyncSession, tenant_id: str, workspace_id: str
) -> Workspace:
    return await WorkspaceRepository(session).require_for_tenant(
        tenant_id, workspace_id
    )
