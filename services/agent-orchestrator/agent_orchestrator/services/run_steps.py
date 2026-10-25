"""run 步骤视图(S1):把 ``run.step.v1`` 事件投影成对外的步骤时间线。

零迁移:step 身份与元数据都固化在 append-only 的事件 payload 里(run_events
是审计流水),这里只读投影;将来要物化 run_steps 表(07 S3,回退/续跑需要
step↔checkpoint 映射)时再换读端,对外契约不变。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph import steps
from agent_orchestrator.repositories import RunEventRepository
from agent_orchestrator.schemas.runs import StepRead
from agent_orchestrator.services.run_lifecycle import require_run


def _step_from_payload(seq: int, payload: dict[str, Any]) -> StepRead:
    """事件 payload → 步骤视图;缺字段给出保守默认(投影不因脏数据整体失败)。"""
    return StepRead(
        seq=seq,
        step_id=str(payload.get("step_id") or ""),
        node=str(payload.get("node") or ""),
        phase=str(payload.get("phase") or ""),
        attempt=int(payload.get("attempt") or 0),
        round=int(payload.get("round") or 0),
        status=str(payload.get("status") or "completed"),
        started_at=payload.get("started_at"),
        finished_at=payload.get("finished_at"),
        duration_ms=payload.get("duration_ms"),
    )


async def list_steps(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[StepRead]:
    """该 run 的步骤时间线,按 seq 升序(与事件回放同序,可直接定位"第几步")。"""
    await require_run(session, tenant_id, run_id)
    events = await RunEventRepository(session).list_for_run(tenant_id, run_id)
    return [
        _step_from_payload(event.seq, event.payload)
        for event in events
        if event.type == steps.EVENT_TYPE
    ]
