"""崩溃续跑 sweeper(见 docs/design/recovery.md §8)。

为什么需要:worker 在执行图的过程中被杀(进程退出/机器重启/OOM)时,run 的
状态可能已经随增量提交落库成 planning/running,而正在执行的 job 事务回滚。
旧 job 租约过期后被重领,按其原动作(plan/approve/...)重放只会在状态守卫上
409,重试耗尽后 job 落 failed——run 就永久挂在 planning/running,只能等超时
sweeper 兜底(默认关闭)或人工处理。

本 sweeper 找出"静默超过 grace、且没有任何 live job(queued 或租约未过期的
running)"的执行中 run,入队 recover job;worker 从最近的 checkpoint 续跑
(见 run_recovery.recover_run)。有 live job 时不动——那个 job 正在推进,
续跑会和它抢同一条图线程。

与超时 sweeper 的分工:这里只负责"把丢掉的执行接回正轨",不判失败;超时
仍由 run_timeout_sweeper 按总时长收场。两者可以同时存在。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.repositories import JobRepository, RunRepository
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.run_lifecycle import require_run
from agent_orchestrator.services.run_recovery import RECOVERABLE_STATUSES

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)


async def sweep_once(container: AppContainer) -> int:
    """扫一轮孤儿执行,返回实际入队续跑的条数;单条失败只记日志。"""
    settings = container.settings
    factory = container.session_factory
    if not settings.jobs_enabled or factory is None:
        return 0
    async with session_scope(factory) as session:
        candidates = await RunRepository(session).list_orphaned_executions(
            datetime.now(UTC), grace_seconds=settings.run_recovery_grace_seconds
        )
        targets = [(run.account_id, run.id) for run in candidates]
    handled = 0
    for account_id, run_id in targets:
        try:
            async with session_scope(factory) as session:
                if await _recover_one(session, account_id, run_id):
                    handled += 1
        except Exception as exc:  # noqa: BLE001 - 后台任务不能因单条失败退出
            logger.warning("run recovery sweep failed", run_id=run_id, error=str(exc))
    return handled


async def _recover_one(session: AsyncSession, account_id: str, run_id: str) -> bool:
    """复核状态与 job 租约后入队 recover;扫描后状态已变则跳过。"""
    run = await require_run(session, account_id, run_id)
    if run.status not in RECOVERABLE_STATUSES:
        return False
    live = await JobRepository(session).has_live_for_run(
        account_id, run_id, datetime.now(UTC)
    )
    if live:
        return False
    await jobs_service.enqueue(session, account_id, "recover", run_id)
    logger.info(
        "run recovery enqueued",
        run_id=run_id,
        account_id=account_id,
        from_status=run.status,
    )
    return True


async def sweeper_loop(container: AppContainer) -> None:
    interval = max(container.settings.run_recovery_sweep_interval_seconds, 0.1)
    while True:
        try:
            await sweep_once(container)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("run recovery sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-run-recovery"
    )
