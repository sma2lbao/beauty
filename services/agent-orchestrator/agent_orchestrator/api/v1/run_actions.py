"""run 生命周期动作端点:规划/审批/拒绝/恢复/重试/确认(§6.2)。

从 runs.py 拆出(R-29):只做依赖装配与 service 调用,状态机在 services 层。
"""

from __future__ import annotations

from typing import Annotated

from common.artifacts import ArtifactStore
from fastapi import APIRouter, Depends
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_artifact_store,
    get_file_downloader,
    get_run_graph,
    get_session,
    get_settings,
    get_tenant_id,
    get_workspace_client,
)
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.runs import (
    CancelRequest,
    ConfirmRequest,
    PlanRead,
    PlanUpdate,
    RejectRequest,
    RunAnswerRead,
    RunAnswerRequest,
    RunRead,
    RunStatus,
)
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.workspace import Workspace

router = APIRouter(prefix="/runs", tags=["runs"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]
ArtifactStoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]
WorkspaceDep = Annotated[Workspace | None, Depends(get_workspace_client)]
FileDownloaderDep = Annotated[FileDownloader | None, Depends(get_file_downloader)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _plan_read(run_id: str, run_status: str, markdown: str) -> PlanRead:
    return PlanRead(run_id=run_id, status=RunStatus(run_status), plan_markdown=markdown)


@router.post("/{run_id}/plan", response_model=PlanRead)
async def plan_run(
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
    settings: SettingsDep,
) -> PlanRead:
    """生成计划;无 DANGER 工具时自动放行并执行到终态(§6.2)。

    有危险工具(未信任 MCP 的对外操作)时仍停在 awaiting_approval 等人工放行。
    """
    run = await runs_service.plan_run(
        session,
        graph,
        tenant_id,
        run_id,
        jobs_enabled=settings.jobs_enabled,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
    )
    return _plan_read(run.id, run.status, run.plan_markdown or "")


@router.patch("/{run_id}/plan", response_model=PlanRead)
async def update_run_plan(
    body: PlanUpdate,
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
) -> PlanRead:
    """审批前编辑计划(§6.1 可编辑工件)。"""
    run = await runs_service.update_plan(
        session, graph, tenant_id, run_id, body.plan_markdown
    )
    return _plan_read(run.id, run.status, run.plan_markdown or "")


@router.post("/{run_id}/approve", response_model=RunRead)
async def approve_run(
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
    settings: SettingsDep,
) -> RunRead:
    """放行审批闸,执行到完成(§6.2 awaiting_approval → completed)。"""
    run = await runs_service.approve_run(
        session,
        graph,
        tenant_id,
        run_id,
        store,
        workspace,
        jobs_enabled=settings.jobs_enabled,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
    )
    return RunRead.model_validate(run)


@router.post("/{run_id}/reject", response_model=RunRead)
async def reject_run(
    body: RejectRequest,
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
) -> RunRead:
    """拒绝计划,run 进入 blocked(§6.2;可 resume 重新进闸)。"""
    run = await runs_service.reject_run(session, graph, tenant_id, run_id, body.reason)
    return RunRead.model_validate(run)


@router.post("/{run_id}/resume", response_model=PlanRead)
async def resume_run(
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
    settings: SettingsDep,
) -> PlanRead:
    """blocked → 重新进审批闸,保留已编辑计划(§6.2 goal 语义)。"""
    run = await runs_service.resume_run(
        session,
        graph,
        tenant_id,
        run_id,
        jobs_enabled=settings.jobs_enabled,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
    )
    return _plan_read(run.id, run.status, run.plan_markdown or "")


@router.post("/{run_id}/retry", response_model=RunRead, status_code=201)
async def retry_run(
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
    settings: SettingsDep,
) -> RunRead:
    """failed → 另起一跑并继承目标/验收标准/已批准计划(§6.2);返回**新 run**。"""
    run = await runs_service.retry_run(
        session,
        graph,
        tenant_id,
        run_id,
        jobs_enabled=settings.jobs_enabled,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
    )
    return RunRead.model_validate(run)


@router.post("/{run_id}/confirm", response_model=RunRead)
async def confirm_run(
    body: ConfirmRequest,
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
    settings: SettingsDep,
) -> RunRead:
    """确认写操作(§6.2):放行则执行并继续,拒绝则跳过并继续验收。"""
    run = await runs_service.confirm_run(
        session,
        graph,
        tenant_id,
        run_id,
        body.action,
        body.reason,
        store,
        workspace,
        jobs_enabled=settings.jobs_enabled,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
    )
    return RunRead.model_validate(run)


@router.post("/{run_id}/answer", response_model=RunAnswerRead, status_code=202)
async def answer_run(
    body: RunAnswerRequest,
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
    settings: SettingsDep,
) -> RunAnswerRead:
    """回答/放弃挂起的提问(§10.5):awaiting_input → running(或入队 answer job)。"""
    run = await runs_service.answer_run(
        session,
        graph,
        tenant_id,
        run_id,
        question_id=body.question_id,
        action=body.action,
        answers=[item.model_dump() for item in body.answers],
        store=store,
        workspace=workspace,
        jobs_enabled=settings.jobs_enabled,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
    )
    return RunAnswerRead(run=RunRead.model_validate(run), accepted=True)


@router.post("/{run_id}/cancel", response_model=RunRead)
async def cancel_run(
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
    body: CancelRequest | None = None,
) -> RunRead:
    """取消 run(P0-2):非终态 → cancelled,该 run 排队中的 job 一并作废。"""
    run = await runs_service.cancel_run(
        session, tenant_id, run_id, body.reason if body else None
    )
    return RunRead.model_validate(run)
