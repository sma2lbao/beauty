"""run 取消(P0-2):非终态 → cancelled,在下一个节点边界真正停下。

取消语义:
- 状态立刻落 cancelled 并记 run.cancelled.v1,前端/SSE 立即看到终态;
- 还在排队的 job 直接置 cancelled,不再被 worker 领取;
- 已 running 的图调用在**下一个安全边界**被打断(S3,design/recovery.md §9):
  节点入口、每次模型调用前、每次工具执行前都会问一次取消探针
  (services/run_cancellation.RunCancellationProbe,读的是库里的 status),
  已取消则抛 graph.cancellation.RunCancelled,图在两次副作用之间停下——不会
  把一次工具调用劈成两半;已发生的写由 S1 稳定幂等键兜底,重放不二次生效;
- 执行边界回来时读库发现 cancelled,跳过终态覆盖(见 _apply_graph_result 与
  run_events._fail_run 的 is_superseded),取消方写的 status_reason 不被覆盖;
- 跨进程同样成立:探针每次查库,不依赖进程内信号;进程内也不走旁路信号。

极端竞态(取消恰好落在一次工具调用执行中)仍可能让这一次调用跑完,但不会
再有下一次;残留风险属"取消的边界粒度",不是重复副作用。
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
    account_id: str,
    run_id: str,
    reason: str | None = None,
) -> Run:
    """取消一个非终态 run;reason 落 status_reason 与 run.cancelled.v1 载荷。"""
    run = await require_run(session, account_id, run_id)
    require_transition(run, "cancel")
    text = (reason or "").strip() or DEFAULT_REASON
    run.status = RunStatus.CANCELLED.value
    run.status_reason = text[:500]
    cancelled_jobs = await JobRepository(session).cancel_queued_for_run(
        run.account_id, run.id
    )
    await append_event(
        session,
        run,
        "run.cancelled.v1",
        {"reason": text, "cancelled_jobs": cancelled_jobs},
    )
    metrics.observe_run_outcome(status=RunStatus.CANCELLED.value)
    return run
