"""run 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_run_graph, get_session, get_tenant_id
from agent_orchestrator.schemas.runs import (
    PlanRead,
    PlanUpdate,
    RejectRequest,
    RunCreate,
    RunEventRead,
    RunRead,
    RunStatus,
)
from agent_orchestrator.services import runs as runs_service

router = APIRouter(prefix="/runs", tags=["runs"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]


def _plan_read(run_id: str, run_status: str, markdown: str) -> PlanRead:
    return PlanRead(run_id=run_id, status=RunStatus(run_status), plan_markdown=markdown)


@router.post("", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: RunCreate, session: DBSession, tenant_id: TenantId
) -> RunRead:
    run = await runs_service.create_run(session, tenant_id, body)
    return RunRead.model_validate(run)


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


@router.post("/{run_id}/plan", response_model=PlanRead)
async def plan_run(
    run_id: str, session: DBSession, tenant_id: TenantId, graph: RunGraph
) -> PlanRead:
    """生成计划并进入人工审批(§6.2 pending → awaiting_approval)。"""
    run = await runs_service.plan_run(session, graph, tenant_id, run_id)
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
    run_id: str, session: DBSession, tenant_id: TenantId, graph: RunGraph
) -> RunRead:
    """放行审批闸,执行到完成(§6.2 awaiting_approval → completed)。"""
    run = await runs_service.approve_run(session, graph, tenant_id, run_id)
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
    run_id: str, session: DBSession, tenant_id: TenantId, graph: RunGraph
) -> PlanRead:
    """blocked → 重新进审批闸,保留已编辑计划(§6.2 goal 语义)。"""
    run = await runs_service.resume_run(session, graph, tenant_id, run_id)
    return _plan_read(run.id, run.status, run.plan_markdown or "")
