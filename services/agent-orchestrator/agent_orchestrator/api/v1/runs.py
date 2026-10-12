"""run 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_tenant_id
from agent_orchestrator.schemas.runs import RunCreate, RunEventRead, RunRead
from agent_orchestrator.services import runs as runs_service

router = APIRouter(prefix="/runs", tags=["runs"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]


@router.post("", response_model=RunRead, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: RunCreate, session: DBSession, tenant_id: TenantId
) -> RunRead:
    run = await runs_service.create_run(session, tenant_id, body)
    return RunRead.model_validate(run)


@router.get("/{run_id}", response_model=RunRead)
async def get_run(run_id: str, session: DBSession, tenant_id: TenantId) -> RunRead:
    run = await runs_service.get_run(session, tenant_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunRead.model_validate(run)


@router.get("/{run_id}/events", response_model=list[RunEventRead])
async def list_run_events(
    run_id: str, session: DBSession, tenant_id: TenantId
) -> list[RunEventRead]:
    """事件回放:按 seq 升序(§6.3;SSE 断线重放与审计共用此读端)。"""
    events = await runs_service.list_events(session, tenant_id, run_id)
    if not events:
        run = await runs_service.get_run(session, tenant_id, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
    return [RunEventRead.model_validate(event) for event in events]
