"""job 入队与状态(M0-04)。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories import JobRepository, RunRepository

KINDS = frozenset(
    {
        "plan",
        "approve",
        "confirm",
        "resume",
        "retry",
        # 中断超时 sweeper 用(P0-2):审批超时自动拒绝,走 blocked
        "reject",
        # 交互式提问的回答(§10.5):POST /answer 或问题超时自动 decline
        "answer",
        # 回退重跑(P1-C):API 侧已推进状态,worker 从 checkpoint 继续跑后续节点
        "rewind",
    }
)


async def enqueue(
    session: AsyncSession,
    account_id: str,
    kind: str,
    run_id: str,
    *,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
    priority: int | None = None,
) -> Job:
    """同一 run 同类 job 已在队列时复用,避免连点重复执行。

    priority(P1-C):不传则从 run 复制(建 run 时的调度优先级);调用方显式给值时
    以调用方为准(如运维手动提升)。run 不存在时按 0 处理。
    """
    if kind not in KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    repo = JobRepository(session)
    existing = await repo.find_queued(account_id, kind, run_id)
    if existing is not None:
        return existing
    if priority is None:
        run = await RunRepository(session).get_for_account(account_id, run_id)
        priority = run.priority if run is not None else 0
    job = Job(
        account_id=account_id,
        kind=kind,
        run_id=run_id,
        priority=priority,
        payload=payload or {},
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        run_at=datetime.now(UTC),
    )
    return await repo.add(job)
