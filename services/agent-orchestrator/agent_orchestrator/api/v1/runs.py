"""run 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from common.errors import AppError
from database.session import session_scope
from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.api.deps import get_run_graph, get_session, get_tenant_id
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import RunEvent
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


TERMINAL_STATUSES = (RunStatus.COMPLETED.value, RunStatus.FAILED.value)


def _sse_chunk(event: RunEvent) -> str:
    data = RunEventRead.model_validate(event).model_dump_json()
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


async def _event_stream(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    tenant_id: str,
    run_id: str,
    after: int,
) -> AsyncIterator[str]:
    """先重放 after 之后的事件,再轮询增量;run 终态后发 stream.end 收尾。

    读端契约:重放与增量一律读 run_events(§6.3);Redis Streams 直播是
    阶段 B 的传输优化,换传输不改读端。
    """
    last_seq = after
    idle_polls = 0
    while True:
        async with session_scope(factory) as session:
            events = await runs_service.list_events(
                session, tenant_id, run_id, after=last_seq
            )
            run = await runs_service.require_run(session, tenant_id, run_id)
        for event in events:
            last_seq = event.seq
            yield _sse_chunk(event)
        if run.status in TERMINAL_STATUSES:
            yield f"event: stream.end\ndata: {json.dumps({'status': run.status})}\n\n"
            return
        if not events:
            idle_polls += 1
            if idle_polls % settings.sse_keepalive_polls == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(settings.sse_poll_interval_seconds)


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
    if last_event_id:
        try:
            after = int(last_event_id)
        except ValueError:
            raise AppError("sse.bad_cursor", "Last-Event-ID 必须是整数") from None
    else:
        after = 0
    async with session_scope(factory) as session:
        await runs_service.require_run(session, tenant_id, run_id)  # 404 校验
    return StreamingResponse(
        _event_stream(factory, settings, tenant_id, run_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
