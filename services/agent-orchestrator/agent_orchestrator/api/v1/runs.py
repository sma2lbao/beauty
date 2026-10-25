"""run 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from common.artifacts import ArtifactStore
from common.errors import ConflictError, NotFoundError
from database.session import session_scope
from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.api.deps import (
    get_artifact_store,
    get_file_downloader,
    get_run_graph,
    get_session,
    get_settings,
    get_skills,
    get_tenant_id,
    get_workspace_client,
)
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import RunArtifact
from agent_orchestrator.schemas.runs import (
    ArtifactMetaRead,
    ArtifactRead,
    ConfirmRequest,
    InstructionCreate,
    InstructionRead,
    PlanRead,
    PlanUpdate,
    RejectRequest,
    RunCreate,
    RunEventRead,
    RunRead,
    RunStatus,
)
from agent_orchestrator.services import artifacts as artifacts_service
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services import skill_packages
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.run_stream import (
    event_stream as _event_stream,
)
from agent_orchestrator.services.run_stream import (
    parse_last_event_id,
)
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace

router = APIRouter(prefix="/runs", tags=["runs"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]
ArtifactStoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]
WorkspaceDep = Annotated[Workspace | None, Depends(get_workspace_client)]
FileDownloaderDep = Annotated[FileDownloader | None, Depends(get_file_downloader)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SkillsDep = Annotated[SkillRegistry, Depends(get_skills)]


def _plan_read(run_id: str, run_status: str, markdown: str) -> PlanRead:
    return PlanRead(run_id=run_id, status=RunStatus(run_status), plan_markdown=markdown)


@router.post("", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: RunCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    skills: SkillsDep,
) -> RunRead:
    skill = None
    if body.skill_id:
        # 内置优先,其次租户上传包的最新版本;两者都没有 → 404(不建半残 run)
        skill = await skill_packages.resolve_skill(
            session, tenant_id, body.skill_id, skills
        )
        if skill is None:
            raise NotFoundError("skill.not_found", "Skill 不存在")
    if body.mcp_server_ids:
        # 显式引用的 MCP server 在建 run 前校验:未知 404、停用/未启用 409
        if not settings.mcp_enabled:
            raise ConflictError("mcp.disabled", "MCP 能力未启用")
        servers = await mcp_service.resolve_selected(
            session, tenant_id, body.mcp_server_ids
        )
        body = body.model_copy(
            update={"mcp_server_ids": [server.id for server in servers]}
        )
    run = await runs_service.create_run(session, tenant_id, body, skill=skill)
    if settings.jobs_enabled:
        await jobs_service.enqueue(session, tenant_id, "plan", run.id)
    return RunRead.model_validate(run)


@router.get("", response_model=list[RunRead])
async def list_runs(
    session: DBSession,
    tenant_id: TenantId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunRead]:
    """租户内 run 列表,最新在前(控制台首页)。"""
    runs = await runs_service.list_runs(session, tenant_id, limit)
    return [RunRead.model_validate(run) for run in runs]


@router.get("/{run_id}", response_model=RunRead)
async def get_run(run_id: str, session: DBSession, tenant_id: TenantId) -> RunRead:
    run = await runs_service.require_run(session, tenant_id, run_id)
    return RunRead.model_validate(run)


@router.get("/{run_id}/events", response_model=list[RunEventRead])
async def list_run_events(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[RunEventRead]:
    """事件回放:按 seq 升序(§6.3;SSE 断线重放与审计共用此读端)。"""
    await runs_service.require_run(session, tenant_id, run_id)
    events = await runs_service.list_events(session, tenant_id, run_id)
    return [RunEventRead.model_validate(event) for event in events]


@router.get("/{run_id}/plan", response_model=PlanRead)
async def get_run_plan(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> PlanRead:
    run = await runs_service.require_run(session, tenant_id, run_id)
    return _plan_read(run.id, run.status, run.plan_markdown or "")


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


@router.post(
    "/{run_id}/instructions",
    response_model=InstructionRead,
    status_code=status.HTTP_201_CREATED,
)
async def queue_instruction(
    body: InstructionCreate,
    run_id: str,
    session: DBSession,
    tenant_id: TenantId,
) -> InstructionRead:
    """追加指令(§6.5):排队,在下一个图边界(规划/放行/确认)被消费。"""
    instruction = await runs_service.queue_instruction(
        session, tenant_id, run_id, body.content
    )
    return InstructionRead.model_validate(instruction)


@router.get("/{run_id}/instructions", response_model=list[InstructionRead])
async def list_instructions(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[InstructionRead]:
    """指令队列:queued/consumed 都返回(控制台展示用)。"""
    instructions = await runs_service.list_instructions(session, tenant_id, run_id)
    return [InstructionRead.model_validate(item) for item in instructions]


@router.get("/{run_id}/events/stream")
async def stream_run_events(
    run_id: str,
    request: Request,
    tenant_id: TenantId,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE 事件流:浏览器断线重连自动带 Last-Event-ID,从 seq 游标续传。"""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    settings: Settings = request.app.state.settings
    after = parse_last_event_id(last_event_id)
    async with session_scope(factory) as session:
        await runs_service.require_run(session, tenant_id, run_id)  # 404 校验
    return StreamingResponse(
        _event_stream(factory, settings, tenant_id, run_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
