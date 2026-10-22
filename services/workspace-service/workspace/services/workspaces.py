"""工作区业务逻辑:幂等默认工作区、创建/更新/删除/列表/详情。

数据访问经 `workspace.repositories`;本层只写业务规则,不手写租户过滤。
"""

from __future__ import annotations

import structlog
from common.artifacts import ArtifactStore
from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.workspace import Workspace, WorkspaceArtifact
from workspace.repositories import ArtifactRepository, WorkspaceRepository
from workspace.schemas.workspace import WorkspaceCreate, WorkspaceUpdate

logger = structlog.get_logger(__name__)


async def require_or_create_default(
    session: AsyncSession, tenant_id: str, default_name: str
) -> Workspace:
    """取租户的默认工作区;没有就建(幂等,供交付与首屏使用)。

    识别顺序:

    1. 先按 `is_default` 标记取(取最早一行)——默认工作区被改名后仍然认得出;
    2. 再按历史名称兜底(迁移前的老数据没有标记),命中后补上标记;
    3. 都没有才新建,并直接标为默认。

    这样"重命名默认工作区"不会导致下一次创建交付物时又新建一个默认工作区。
    """
    workspaces = WorkspaceRepository(session)
    marked = await workspaces.list_scoped(
        tenant_id,
        Workspace.is_default.is_(True),
        order_by=[Workspace.created_at, Workspace.id],
        limit=1,
    )
    if marked:
        return marked[0]
    legacy = await workspaces.list_scoped(
        tenant_id,
        Workspace.name == default_name,
        order_by=[Workspace.created_at, Workspace.id],
        limit=1,
    )
    if legacy:
        legacy[0].is_default = True
        await session.flush()
        return legacy[0]
    workspace = Workspace(
        tenant_id=tenant_id,
        name=default_name,
        description="自动创建:租户默认工作区",
        status="active",
        is_default=True,
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


async def update_workspace(
    session: AsyncSession,
    tenant_id: str,
    workspace_id: str,
    data: WorkspaceUpdate,
) -> Workspace:
    """改工作区名称/描述:同一租户内名称仍然唯一,重名 409。

    未传字段保持不变;description 显式传 `null` 表示清空(见 WorkspaceUpdate)。
    """
    workspaces = WorkspaceRepository(session)
    workspace = await workspaces.require_for_tenant(tenant_id, workspace_id)
    if data.name is not None:
        duplicate = await workspaces.list_scoped(
            tenant_id,
            Workspace.name == data.name,
            Workspace.id != workspace.id,
        )
        if duplicate:
            raise ConflictError(
                "workspace.exists",
                "同名工作区已存在",
                details={"workspace_id": duplicate[0].id, "name": data.name},
            )
        workspace.name = data.name
    if "description" in data.model_fields_set:
        workspace.description = data.description
    await session.flush()
    return workspace


async def delete_workspace(
    session: AsyncSession,
    tenant_id: str,
    workspace_id: str,
    store: ArtifactStore | None,
) -> None:
    """删除工作区:先清理其交付物(对象 + 行),再删工作区本身。

    默认工作区由系统维护(交付与编排默认往它落),不允许删除 → 409
    `workspace.default_protected`;需要"换掉"默认工作区可以改名,或另建工作区。

    级联只发生在**本服务内**:对象存储里的字节与 `workspace_artifacts` 行一起
    清掉。跨服务的引用(agent-orchestrator 的会话/run、沙箱实例)都是**弱引用**
    (只存 id,没有外键),本服务不越权清理——编排侧删除后会话落"未分组",
    交付物的 run 溯源随之失效,这是有意接受的弱引用代价。

    单个对象删除失败只记 warning 继续,不让一个坏对象把整次删除变成 500
    (行仍会删掉,对象成为孤儿,可后续按对象键排查)。
    """
    workspaces = WorkspaceRepository(session)
    workspace = await workspaces.require_for_tenant(tenant_id, workspace_id)
    if workspace.is_default:
        raise ConflictError(
            "workspace.default_protected",
            "默认工作区由系统维护,不能删除",
            details={"workspace_id": workspace.id},
        )
    artifacts = ArtifactRepository(session)
    records = await artifacts.list_scoped(
        tenant_id, WorkspaceArtifact.workspace_id == workspace_id
    )
    for record in records:
        if store is None or not record.object_key:
            continue
        try:
            await store.delete(record.object_key)
        except Exception as exc:
            logger.warning(
                "workspace artifact object delete failed",
                workspace_id=workspace_id,
                object_key=record.object_key,
                error=str(exc),
            )
    await artifacts.delete_where(
        tenant_id, WorkspaceArtifact.workspace_id == workspace_id
    )
    await workspaces.delete(workspace)


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
