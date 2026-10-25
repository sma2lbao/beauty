"""job 入队与状态(M0-04)。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Job
from agent_orchestrator.repositories import JobRepository

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
    }
)


async def enqueue(
    session: AsyncSession,
    tenant_id: str,
    kind: str,
    run_id: str,
    *,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
) -> Job:
    """同一 run 同类 job 已在队列时复用,避免连点重复执行。"""
    if kind not in KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    repo = JobRepository(session)
    existing = await repo.find_queued(tenant_id, kind, run_id)
    if existing is not None:
        return existing
    job = Job(
        tenant_id=tenant_id,
        kind=kind,
        run_id=run_id,
        payload=payload or {},
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        run_at=datetime.now(UTC),
    )
    return await repo.add(job)
