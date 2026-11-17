"""子代理等待 sweeper(L2,见 docs/design/subagents.md §6.1)。

父 run 停在 awaiting_subagent 等子 run 终态唤醒(subagent_wake.notify_parent)。
两条路会把它永久挂住:

- **漏唤醒**:子 run 已经终态,但唤醒 job 因进程崩溃或队列丢失没落地——父 run
  没有任何 live job,永远等不到;崩溃续跑 sweeper 只扫 planning/running,接不住
  停在 awaiting_subagent 的 run。
- **等太久**:子 run 本身卡在长任务,或停在需要人工的 awaiting_*(人也不会再来)。

本 sweeper 每轮扫 awaiting_subagent:

1. 子 run 全部终态、父 run 又没有 live job → 重排 subagent job(漏唤醒补偿;
   resume_subagent 幂等:先把状态改 running 再续图,重复触发无害);
2. 仍有在飞子 run 且最老的子 run 已超过 subagent_wait_timeout_seconds → 逐个
   cancel_run(原因「子代理等待超时」),再确保父 run 有一条唤醒 job——父 run 拿到
   cancelled 回执后继续往下走。

超时从**等待方**发起(级联取消),不是子 run 自己超时:子 run 的
subagent_timeout_seconds 只约束内联模式;异步模式下子 run 是一条独立 job,没人
在它身上计时。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import PARENT_KIND_SUBAGENT, Run
from agent_orchestrator.repositories import JobRepository, RunRepository
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services.run_cancel import cancel_run
from agent_orchestrator.services.run_lifecycle import require_run
from agent_orchestrator.services.run_subagent_resume import WAITING_STATUS
from agent_orchestrator.services.subagents_spec import _TERMINAL_STATUSES

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

#: 等待超时后给子 run 的理由(落在 status_reason 与 run.cancelled.v1)。
WAIT_TIMEOUT_REASON = "子代理等待超时"

logger = structlog.get_logger(__name__)


async def sweep_once(container: AppContainer) -> int:
    """扫一轮等待中的父 run,返回实际推进(补偿或超时)的条数。"""

    settings = container.settings
    factory = container.session_factory
    if not settings.jobs_enabled or factory is None:
        return 0
    async with session_scope(factory) as session:
        candidates = await RunRepository(session).list_awaiting_subagents()
        targets = [(run.account_id, run.id) for run in candidates]
    handled = 0
    for account_id, run_id in targets:
        try:
            async with session_scope(factory) as session:
                if await _sweep_one(session, container, account_id, run_id):
                    handled += 1
        except Exception as exc:  # noqa: BLE001 - 后台任务不能因单条失败退出
            logger.warning("subagent wait sweep failed", run_id=run_id, error=str(exc))
    return handled


async def _sweep_one(
    session: AsyncSession,
    container: AppContainer,
    account_id: str,
    run_id: str,
) -> bool:
    """复核父 run 与子 run 后决定补偿唤醒还是超时取消;状态已变则跳过。"""

    run = await require_run(session, account_id, run_id)
    if run.status != WAITING_STATUS:
        return False
    children = await _children(session, run)
    if not children:
        # 没有子 run 是数据异常:交给 resume_subagent 落 failed,这里不动
        return False
    now = datetime.now(UTC)
    if all(child.status in _TERMINAL_STATUSES for child in children):
        if await JobRepository(session).has_live_for_run(account_id, run_id, now):
            return False
        await _enqueue_wake(session, account_id, run_id)
        logger.info("subagent wake repaired", run_id=run_id, account_id=account_id)
        return True
    timeout = container.settings.subagent_wait_timeout_seconds
    if timeout <= 0:
        return False
    oldest = _aware(min(child.created_at for child in children))
    if oldest is not None and now - oldest < timedelta(seconds=timeout):
        return False
    for child in children:
        if child.status in _TERMINAL_STATUSES:
            continue
        # cancel_run 内部会 notify_parent(入队唤醒),所以下面只需要兜底
        await cancel_run(session, account_id, child.id, WAIT_TIMEOUT_REASON)
    if not await JobRepository(session).has_live_for_run(account_id, run_id, now):
        await _enqueue_wake(session, account_id, run_id)
    logger.info(
        "subagent wait timed out",
        run_id=run_id,
        account_id=account_id,
        children=[child.id for child in children],
        timeout_seconds=timeout,
    )
    return True


async def _children(session: AsyncSession, parent: Run) -> list[Run]:
    """父 run 直系子代理(与 resume_subagent 同一口径,按建立时间排序)。"""

    return await RunRepository(session).list_scoped(
        parent.account_id,
        Run.parent_run_id == parent.id,
        Run.parent_kind == PARENT_KIND_SUBAGENT,
        order_by=[Run.created_at, Run.id],
    )


async def _enqueue_wake(session: AsyncSession, account_id: str, run_id: str) -> None:
    """重排一条唤醒 job;dedupe=False,不合并(合并会吃掉两次独立的唤醒)。"""

    await jobs_service.enqueue(session, account_id, "subagent", run_id, dedupe=False)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite 读回来的时间没有时区,统一按 UTC 补上再比较。"""

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def sweeper_loop(container: AppContainer) -> None:
    interval = max(container.settings.subagent_wait_sweep_interval_seconds, 0.1)
    while True:
        try:
            await sweep_once(container)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("subagent wait sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""

    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-subagent-wait"
    )
