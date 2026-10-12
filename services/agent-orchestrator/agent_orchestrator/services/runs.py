"""run 编排业务逻辑:不 import FastAPI,可独立单测(分层约定见架构 §4)。

graph 生命周期(架构 §6.2):

- plan:新 attempt 起图(thread = ``<run_id>#<attempt>``),跑到审批闸中断
  → awaiting_approval;
- approve:放行闸门,execute → verify(返工上限见 builder)→ deliver → completed;
- reject:拒绝计划,图终结 → blocked;resume:新 attempt 重新进闸,保留已编辑计划;
- 状态守卫:非法迁移 ConflictError(409),资源不存在 NotFoundError(404),
  对外渲染走 common 统一错误模型。
"""

from __future__ import annotations

from typing import Any

from common.errors import ConflictError, NotFoundError
from events.envelope import EventEnvelope
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
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


def _config(run: Run) -> dict[str, Any]:
    """graph 线程标识:同一 run 的每次 blocked→resume 周期一个新线程。"""
    return {"configurable": {"thread_id": f"{run.id}#{run.attempts}"}}


async def require_run(session: AsyncSession, tenant_id: str, run_id: str) -> Run:
    """取租户内 run;不存在 → NotFoundError(404)。"""
    run = await session.scalar(
        select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id)
    )
    if run is None:
        raise NotFoundError("run.not_found", "run 不存在", details={"run_id": run_id})
    return run


def _require_status(run: Run, *allowed: RunStatus) -> None:
    if run.status not in {status.value for status in allowed}:
        raise ConflictError(
            "run.invalid_state",
            f"run 状态 {run.status} 不允许该操作",
            details={"run_id": run.id, "status": run.status},
        )


async def _fail_run(
    session: AsyncSession, run: Run, exc: Exception, stage: str
) -> None:
    """图执行异常 → failed 终态(§6.2):状态落库 + run.failed.v1,不向调用方裸奔。

    这是图调用边界的统一兜底——节点/LLM/图本身的异常都在这里收敛;
    failed 是终态,重试语义是新起 run。
    """
    run.status = RunStatus.FAILED.value
    run.blocked_reason = f"{type(exc).__name__}: {exc}"[:500]
    await append_event(
        session,
        run,
        "run.failed.v1",
        {"stage": stage, "reason": run.blocked_reason},
    )


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


async def _invoke_plan(
    session: AsyncSession, graph: CompiledStateGraph, run: Run
) -> None:
    """起图跑到审批闸;resume 场景保留用户已编辑的计划。"""
    state: dict[str, Any] = {
        "run_id": run.id,
        "tenant_id": run.tenant_id,
        "title": run.title,
        "goal": run.goal,
        "acceptance_criteria": list(run.acceptance_criteria or []),
    }
    if run.plan_markdown:
        state["plan_markdown"] = run.plan_markdown
    try:
        values = await graph.ainvoke(state, config=_config(run))
    except Exception as exc:
        await _fail_run(session, run, exc, stage="plan")
        return
    run.plan_markdown = values.get("plan_markdown", "")
    run.status = RunStatus.AWAITING_APPROVAL.value
    await append_event(session, run, "run.planned.v1", {"attempt": run.attempts})


async def plan_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.PENDING)
    await _invoke_plan(session, graph, run)
    return run


async def update_plan(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    markdown: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
    await graph.aupdate_state(_config(run), {"plan_markdown": markdown}, as_node="plan")
    run.plan_markdown = markdown
    await append_event(session, run, "run.plan.edited.v1", {"chars": len(markdown)})
    return run


async def approve_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
    # 放行动作先入事件流,再恢复图:执行失败时顺序如实(approved → failed)
    await append_event(session, run, "run.approved.v1", {"attempt": run.attempts})
    try:
        values = await graph.ainvoke(
            Command(resume={"action": "approve"}), config=_config(run)
        )
    except Exception as exc:
        await _fail_run(session, run, exc, stage="execute")
        return run
    run.rounds = values.get("rounds", 0)
    final_status = values.get("final_status")
    if final_status == "failed":
        # 返工耗尽仍未验收:fail_node 的诚实终态(§6.1),事件流如实记录
        run.status = RunStatus.FAILED.value
        run.blocked_reason = (values.get("blocked_reason") or "验收未通过")[:500]
        await append_event(
            session,
            run,
            "run.failed.v1",
            {
                "stage": "verify",
                "reason": run.blocked_reason,
                "rounds": run.rounds,
            },
        )
        return run
    run.status = RunStatus.COMPLETED.value
    await append_event(
        session,
        run,
        "run.completed.v1",
        {"rounds": run.rounds, "results": values.get("results") or []},
    )
    return run


async def reject_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
    reason: str,
) -> Run:
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.AWAITING_APPROVAL)
    await graph.ainvoke(
        Command(resume={"action": "reject", "reason": reason}),
        config=_config(run),
    )
    run.status = RunStatus.BLOCKED.value
    run.blocked_reason = reason
    await append_event(session, run, "run.rejected.v1", {"reason": reason})
    await append_event(
        session,
        run,
        "run.blocked.v1",
        {"reason": reason, "attempt": run.attempts},
    )
    return run


async def resume_run(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    run_id: str,
) -> Run:
    """blocked → 新 attempt 重新进审批闸,计划保留(§6.2 goal 语义)。"""
    run = await require_run(session, tenant_id, run_id)
    _require_status(run, RunStatus.BLOCKED)
    run.attempts += 1
    run.blocked_reason = None
    await append_event(session, run, "run.resumed.v1", {"attempt": run.attempts})
    await _invoke_plan(session, graph, run)
    return run


async def list_events(
    session: AsyncSession, tenant_id: str, run_id: str, after: int = 0
) -> list[RunEvent]:
    """事件回放:按 seq 升序(§6.3,SSE 断线重放与审计共用);after 为续传游标。"""
    rows = await session.scalars(
        select(RunEvent)
        .where(
            RunEvent.run_id == run_id,
            RunEvent.tenant_id == tenant_id,
            RunEvent.seq > after,
        )
        .order_by(RunEvent.seq)
    )
    return list(rows)
