"""工作区业务逻辑:幂等默认工作区、创建/列表/详情。"""

from __future__ import annotations

from common.errors import ConflictError, NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.workspace import Workspace
from workspace.schemas.workspace import WorkspaceCreate


async def require_or_create_default(
    session: AsyncSession, tenant_id: str, default_name: str
) -> Workspace:
    """取租户的默认工作区;没有就建(幂等,供交付与首屏使用)。"""
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.tenant_id == tenant_id, Workspace.name == default_name
        )
    )
    if workspace is None:
        workspace = Workspace(
            tenant_id=tenant_id,
            name=default_name,
            description="自动创建:租户默认工作区",
            status="active",
        )
        session.add(workspace)
        await session.flush()
    return workspace


async def create_workspace(
    session: AsyncSession, tenant_id: str, data: WorkspaceCreate
) -> Workspace:
    duplicate = await session.scalar(
        select(Workspace).where(
            Workspace.tenant_id == tenant_id, Workspace.name == data.name
        )
    )
    if duplicate is not None:
        raise ConflictError(
            "workspace.exists",
            "同名工作区已存在",
            details={"workspace_id": duplicate.id, "name": duplicate.name},
        )
    workspace = Workspace(
        tenant_id=tenant_id,
        name=data.name,
        description=data.description,
        status="active",
    )
    session.add(workspace)
    await session.flush()
    return workspace


async def list_workspaces(session: AsyncSession, tenant_id: str) -> list[Workspace]:
    rows = await session.scalars(
        select(Workspace)
        .where(Workspace.tenant_id == tenant_id)
        .order_by(Workspace.created_at.asc(), Workspace.id.asc())
    )
    return list(rows)


async def require_workspace(
    session: AsyncSession, tenant_id: str, workspace_id: str
) -> Workspace:
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id, Workspace.tenant_id == tenant_id
        )
    )
    if workspace is None:
        raise NotFoundError(
            "workspace.not_found",
            "工作区不存在",
            details={"workspace_id": workspace_id},
        )
    return workspace
