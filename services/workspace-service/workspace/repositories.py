"""工作区域仓储:租户过滤与 404 语义在这里声明一次。"""

from database.repository import TenantRepository

from workspace.models.workspace import Workspace, WorkspaceArtifact


class WorkspaceRepository(TenantRepository[Workspace]):
    model = Workspace
    not_found_code = "workspace.not_found"
    not_found_message = "工作区不存在"
    id_detail_key = "workspace_id"


class ArtifactRepository(TenantRepository[WorkspaceArtifact]):
    model = WorkspaceArtifact
    not_found_code = "artifact.not_found"
    not_found_message = "交付物不存在"
    id_detail_key = "artifact_id"
