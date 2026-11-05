"""工作区域仓储:账号过滤与 404 语义在这里声明一次。"""

from datetime import datetime

from database.repository import AccountRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workspace.models.sandbox import SandboxInstance
from workspace.models.workspace import Workspace, WorkspaceArtifact


class WorkspaceRepository(AccountRepository[Workspace]):
    model = Workspace
    not_found_code = "workspace.not_found"
    not_found_message = "工作区不存在"
    id_detail_key = "workspace_id"


class ArtifactRepository(AccountRepository[WorkspaceArtifact]):
    model = WorkspaceArtifact
    not_found_code = "artifact.not_found"
    not_found_message = "交付物不存在"
    id_detail_key = "artifact_id"


class SandboxRepository(AccountRepository[SandboxInstance]):
    model = SandboxInstance
    not_found_code = "sandbox.not_found"
    not_found_message = "沙箱不存在"
    id_detail_key = "sandbox_id"


class CrossAccountSandboxRepository:
    """回收器专用:跨账号查询过期沙箱(后台任务没有"当前账号")。

    仅用于 `reap_expired`;HTTP 路径一律走 `SandboxRepository`(账号隔离)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_expired(
        self, now: datetime, *, limit: int = 100
    ) -> list[SandboxInstance]:
        statement = (
            select(SandboxInstance)
            .where(
                SandboxInstance.status.in_(("running", "creating")),
                SandboxInstance.expires_at < now,
            )
            .order_by(SandboxInstance.expires_at)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))
