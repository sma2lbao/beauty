"""run 编排业务逻辑:不 import FastAPI,可独立单测(分层约定见架构 §4)。"""

from __future__ import annotations

from typing import Any

from events.envelope import EventEnvelope
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.schemas.runs import RunCreate, RunStatus


async def append_event(
    session: AsyncSession, run: Run, type_: str, payload: dict[str, Any]
) -> RunEvent:
    """追加 run 事件:seq 每 run 单调递增;信封负责命名/版本校验(§6.3)。"""
    env = EventEnvelope.create(type_, payload)
    current_max = await session.scalar(
        select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id)
    )
    event = RunEvent(
        id=env.id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        seq=(current_max or 0) + 1,
        type=env.type,
        payload=env.payload,
        occurred_at=env.occurred_at,
    )
    session.add(event)
    await session.flush()
    return event


async def create_run(session: AsyncSession, tenant_id: str, data: RunCreate) -> Run:
    run = Run(
        tenant_id=tenant_id,
        title=data.title,
        goal=data.goal,
        status=RunStatus.PENDING.value,
        acceptance_criteria=data.acceptance_criteria,
    )
    session.add(run)
    await session.flush()
    await append_event(session, run, "run.created.v1", {"title": run.title})
    return run


async def get_run(session: AsyncSession, tenant_id: str, run_id: str) -> Run | None:
    """租户内取 run:查询永远带 tenant_id 过滤(架构 §7)。"""
    return await session.scalar(
        select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id)
    )


async def list_events(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[RunEvent]:
    """事件回放:按 seq 升序(§6.3,SSE 断线重放与审计共用)。"""
    rows = await session.scalars(
        select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.tenant_id == tenant_id)
        .order_by(RunEvent.seq)
    )
    return list(rows)
