"""run 交付物端点:元数据/正文/原字节下载(§7)。

从 runs.py 拆出(R-29):只做参数校验与 service 调用,不写业务逻辑。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from common.artifacts import ArtifactStore
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_artifact_store,
    get_session,
    get_tenant_id,
)
from agent_orchestrator.models.runs import RunArtifact
from agent_orchestrator.schemas.runs import ArtifactMetaRead, ArtifactRead
from agent_orchestrator.services import artifacts as artifacts_service
from agent_orchestrator.services import runs as runs_service

router = APIRouter(prefix="/runs", tags=["runs"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
ArtifactStoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]


def _artifact_meta(row: RunArtifact) -> ArtifactMetaRead:
    return ArtifactMetaRead(
        id=row.id,
        run_id=row.run_id,
        kind=row.kind,
        title=row.title,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        storage=row.storage,
        source=row.source,
        created_at=row.created_at,
    )


@router.get("/{run_id}/artifact", response_model=ArtifactRead)
async def get_run_artifact(
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    store: ArtifactStoreDep,
) -> ArtifactRead:
    """取主交付物(§7,旧契约):completed 后取件;未交付 → 404 artifact.not_found。

    主交付物优先文本报告;二进制件的 content 为空串,按 `content_type` 走下载端点。
    """
    await runs_service.require_run(session, tenant_id, run_id)
    view = await runs_service.get_artifact(session, tenant_id, run_id, store)
    return ArtifactRead(
        id=view.id,
        run_id=view.run_id,
        kind=view.kind,
        title=view.title,
        content=view.content,
        storage=view.storage,
        size_bytes=view.size_bytes,
        created_at=view.created_at,
        filename=view.filename,
        content_type=view.content_type,
        source=view.source,
    )


@router.get("/{run_id}/artifacts", response_model=list[ArtifactMetaRead])
async def list_run_artifacts(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[ArtifactMetaRead]:
    """该 run 的全部交付物(元数据,不含正文):run 不存在 → 404,未交付 → []。"""
    await runs_service.require_run(session, tenant_id, run_id)
    rows = await artifacts_service.list_artifacts(session, tenant_id, run_id)
    return [_artifact_meta(row) for row in rows]


@router.get("/{run_id}/artifacts/{artifact_id}/download")
async def download_run_artifact(
    run_id: str,
    artifact_id: str,
    session: DBSession,
    tenant_id: TenantId,
    store: ArtifactStoreDep,
) -> Response:
    """下载交付物原始字节(文本/二进制同一入口)。

    不属于该 run/租户的 artifact 一律 404 `artifact.not_found`;
    缺失对象由 service 抛 `artifact.object_missing`(500,存储配置问题)。
    """
    await runs_service.require_run(session, tenant_id, run_id)
    row = await artifacts_service.require_artifact(
        session, tenant_id, run_id, artifact_id
    )
    data = await artifacts_service.read_artifact_bytes(row, store)
    filename = quote(artifacts_service.artifact_download_name(row), safe="")
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
        },
    )
