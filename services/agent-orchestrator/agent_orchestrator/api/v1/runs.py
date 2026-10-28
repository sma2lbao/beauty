"""run 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。

本模块只留 run 的创建/查询、事件回放、指令队列与 SSE 事件流;交付物端点拆到
run_artifacts.py,生命周期动作(规划/审批/重试等)拆到 run_actions.py(R-29),
三者共用 /runs 前缀。
"""

from __future__ import annotations

from typing import Annotated

from common.errors import ConflictError, NotFoundError
from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_container,
    get_llm_client,
    get_session,
    get_settings,
    get_skills,
    get_tenant_id,
)
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.runs import (
    InstructionCreate,
    InstructionRead,
    PlanRead,
    RunCreate,
    RunEventRead,
    RunItemRead,
    RunRead,
    RunStatus,
    RunUsageRead,
    StepRead,
    SubagentRead,
    TodoRead,
)
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import models as models_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services import skill_packages
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.run_stream import (
    ensure_stream_target,
    parse_last_event_id,
)
from agent_orchestrator.services.run_stream import (
    event_stream as _event_stream,
)
from agent_orchestrator.services.skills import SkillRegistry

__all__ = ["_event_stream", "router"]

router = APIRouter(prefix="/runs", tags=["runs"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
SkillsDep = Annotated[SkillRegistry, Depends(get_skills)]
LLMDep = Annotated[LLMClient | None, Depends(get_llm_client)]


def _plan_read(run_id: str, run_status: str, markdown: str) -> PlanRead:
    return PlanRead(run_id=run_id, status=RunStatus(run_status), plan_markdown=markdown)


@router.post("", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: RunCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    skills: SkillsDep,
    llm: LLMDep,
) -> RunRead:
    """建 run:body.model 不在可用模型列表时 400(校验见 services.models)。"""
    await models_service.validate_model(body.model, llm=llm, settings=settings)
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


@router.get("/{run_id}/steps", response_model=list[StepRead])
async def list_run_steps(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[StepRead]:
    """步骤时间线(S1):事件投影,含节点/阶段/轮次与起止时间(零迁移)。"""
    return await runs_service.list_steps(session, tenant_id, run_id)


@router.get("/{run_id}/items", response_model=list[RunItemRead])
async def list_run_items(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[RunItemRead]:
    """过程项视图(P1-D):run.item.v1 按 item_id 合并相位,按首现 seq 排序。

    thread/turn/item 三级分类学的读端;没有 item 的 run 返回空数组(不是 404)。
    """
    return await runs_service.list_items(session, tenant_id, run_id)


@router.get("/{run_id}/subagents", response_model=list[SubagentRead])
async def list_run_subagents(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[SubagentRead]:
    """子代理面板(P1-E):该 run 派生的子代理(按创建时间,空数组合法)。

    先 ``require_run``:跨租户/不存在一律 404,不泄漏存在性;``summary`` 取父级
    ``kind=subagent`` item 里最近一次的完成摘要(未结束为 null)。
    """
    return await runs_service.list_subagents(session, tenant_id, run_id)


@router.get("/{run_id}/usage", response_model=RunUsageRead)
async def get_run_usage(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> RunUsageRead:
    """run 用量汇总(P1-E):聚合本地 ``run.usage.v1``,无用量返回零值。"""
    return await runs_service.run_usage(session, tenant_id, run_id)


@router.get("/{run_id}/todos", response_model=TodoRead)
async def get_run_todos(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> TodoRead:
    """任务清单(M4):runs.todos 快照 + 最新 revision(没有清单也不是 404)。"""
    return await runs_service.list_todos(session, tenant_id, run_id)


@router.get("/{run_id}/plan", response_model=PlanRead)
async def get_run_plan(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> PlanRead:
    run = await runs_service.require_run(session, tenant_id, run_id)
    return _plan_read(run.id, run.status, run.plan_markdown or "")


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
    container = get_container(request)
    assert container.session_factory is not None
    settings: Settings = container.settings
    after = parse_last_event_id(last_event_id)
    await ensure_stream_target(container.session_factory, tenant_id, run_id)
    return StreamingResponse(
        _event_stream(container.session_factory, settings, tenant_id, run_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
