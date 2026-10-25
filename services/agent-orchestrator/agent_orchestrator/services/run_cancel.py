"""run 取消(P0-2):非终态 → cancelled,协作式收手。

取消语义:
- 状态立刻落 cancelled 并记 run.cancelled.v1,前端/SSE 立即看到终态;
- 还在排队的 job 直接置 cancelled,不再被 worker 领取;
- 已 running 的图调用**不强制打断**——节点是纯函数,拿不到会话也不该抛中断;
  执行边界回来时读库发现 cancelled,跳过终态覆盖(见 _apply_graph_result)。
  极端竞态(取消与完成同一时刻)仍可能让已算出的结果先落库,残留风险属 P0-3。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_lifecycle import require_run, require_transition

DEFAULT_REASON = "已取消"


async def cancel_run(
    session: AsyncSession,
    tenant_id: str,
    run_id: str,
    reason: str | None = None,
) -> Run:
    """取消一个非终态 run;reason 落 status_reason 与 run.cancelled.v1 载荷。"""
    run = await require_run(session, tenant_id, run_id)
    require_transition(run, "cancel")
    text = (reason or "").strip() or DEFAULT_REASON
    run.status = RunStatus.CANCELLED.value
    run.status_reason = text[:500]
    cancelled_jobs = await JobRepository(session).cancel_queued_for_run(
        run.tenant_id, run.id
    )
    await append_event(
        session,
        run,
        "run.cancelled.v1",
        {"reason": text, "cancelled_jobs": cancelled_jobs},
    )
    metrics.observe_run_outcome(status=RunStatus.CANCELLED.value)
    return run
