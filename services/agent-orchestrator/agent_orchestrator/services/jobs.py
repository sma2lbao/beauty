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
        # 崩溃续跑(§8):状态已停在 planning/running,worker 从最近的图 checkpoint
        # 继续跑;由 run 恢复 sweeper 入队,或把失效的旧动作 job 改道而来
        "recover",
        # 异步子代理唤醒(L2):子 run 到终态时入队,把父 run 从
        # awaiting_subagent 恢复(子代理未全部终态时只做一次检查)
        "subagent",
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
    dedupe: bool = True,
) -> Job:
    """同一 run 同类 job 已在队列时复用,避免连点重复执行。

    priority(P1-C):不传则从 run 复制(建 run 时的调度优先级);调用方显式给值时
    以调用方为准(如运维手动提升)。run 不存在时按 0 处理。

    dedupe(L2):子代理唤醒必须关掉——两个子 run 几乎同时到终态时如果合并成
    一条 job,worker 可能在后一个子 run 提交前就检查完,把这次唤醒吃掉;
    "唤醒到的 job 先到先跑、后到的发现全部终态再恢复"靠内容检查,不靠合并。
    """
    if kind not in KINDS:
        raise ValueError(f"unknown job kind: {kind}")
    repo = JobRepository(session)
    if dedupe:
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
