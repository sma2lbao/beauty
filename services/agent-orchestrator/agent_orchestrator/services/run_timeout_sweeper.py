"""run 执行超时 sweeper(P1-C,见 docs/design/recovery.md §4)。

为什么需要:run 停在 planning/running/awaiting_* 时若 worker 崩溃、图卡在外部
调用或模型长尾,任务会永久挂着——既占并发配额,也没有任何人能看见"它为什么不动"。
本 sweeper 按 run 级/全局超时把这类 run 条件更新成 failed(可重试),并留下
run.timeout.v1 事件与固定 status_reason。

与 interrupt sweeper 的分工:interrupt 处理"等人工的 awaiting_*"并按既有动作
收场(reject/confirm/answer);这里处理"总时长超时"的兜底终态,不区分 interrupt
类型——即使停在 awaiting_approval,总时长到了也按 failed 收场。

并发正确性:条件 UPDATE(status = 扫描时观测到的状态)保证与用户操作/worker
收尾不打架——0 行说明状态已被别人推进,直接跳过(不再写事件/取消 job)。
只在 jobs_enabled 且生效超时 > 0 时启动(见 AppContainer._assemble_graph)。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from database.session import session_scope
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import JobRepository, RunRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_lifecycle import require_run

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)

TIMEOUT_EVENT = "run.timeout.v1"
#: 超时只对"仍在推进"的状态生效;终态行即使被直接调用也直接跳过
_ACTIVE_STATUSES = frozenset(
    {
        RunStatus.PENDING.value,
        RunStatus.PLANNING.value,
        RunStatus.RUNNING.value,
        RunStatus.AWAITING_APPROVAL.value,
        RunStatus.AWAITING_CONFIRMATION.value,
        RunStatus.AWAITING_INPUT.value,
        RunStatus.BLOCKED.value,
    }
)
#: status_reason 固定文案(§4):前端据此提示"可重试",不要塞异常细节
TIMEOUT_REASON_TEMPLATE = "执行超时（{seconds} 秒），可重试"


async def sweep_once(container: AppContainer) -> int:
    """扫一轮超时 run,返回实际收尾条数;单条失败只记日志,不中断整轮。"""
    settings = container.settings
    factory = container.session_factory
    if not settings.jobs_enabled or factory is None:
        return 0
    if settings.run_timeout_seconds <= 0:
        return 0
    async with session_scope(factory) as session:
        overdue = await RunRepository(session).list_run_timeouts(
            datetime.now(UTC),
            default_timeout_seconds=settings.run_timeout_seconds,
        )
        targets = [(run.account_id, run.id) for run in overdue]
    handled = 0
    for account_id, run_id in targets:
        try:
            async with session_scope(factory) as session:
                if await _timeout_one(session, account_id, run_id, settings):
                    handled += 1
        except Exception as exc:  # noqa: BLE001 - 后台任务不能因单条失败退出
            logger.warning("run timeout sweep failed", run_id=run_id, error=str(exc))
    return handled


def _effective_timeout(run: Run, settings: Settings) -> int:
    """run 显式超时优先,否则用全局默认;<= 0 视为本 run 不超时。"""
    if run.timeout_seconds is not None:
        return run.timeout_seconds
    return settings.run_timeout_seconds


def _as_utc(moment: datetime) -> datetime:
    """SQLite 读回的是 naive datetime(按 UTC 存),统一补时区再算差值。"""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def _timeout_one(
    session: AsyncSession,
    account_id: str,
    run_id: str,
    settings: Settings,
) -> bool:
    """复核状态/超时并条件更新为 failed;扫描后状态已变则返回 False。"""
    run = await require_run(session, account_id, run_id)
    observed = run.status
    if observed not in _ACTIVE_STATUSES:
        return False
    timeout = _effective_timeout(run, settings)
    if timeout <= 0:
        return False
    started = run.started_at or run.created_at
    if started is None:
        return False
    now = datetime.now(UTC)
    elapsed = (now - _as_utc(started)).total_seconds()
    if elapsed < timeout:
        return False
    # 条件更新:读到的 status 必须仍是本次观测值,否则说明用户/worker 已推进状态。
    result = await session.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == observed)
        .values(
            status=RunStatus.FAILED.value,
            status_reason=TIMEOUT_REASON_TEMPLATE.format(seconds=timeout),
            updated_at=now,
        )
    )
    if result.rowcount == 0:
        return False
    # ORM 对象与条件 UPDATE 同步:后续 append_event/日志用同一份视图。
    run.status = RunStatus.FAILED.value
    run.status_reason = TIMEOUT_REASON_TEMPLATE.format(seconds=timeout)
    run.updated_at = now
    started_iso = _as_utc(started).isoformat()
    cancelled_jobs = await JobRepository(session).cancel_queued_for_run(
        run.account_id, run.id
    )
    await append_event(
        session,
        run,
        TIMEOUT_EVENT,
        {
            "timeout_seconds": timeout,
            "started_at": started_iso,
            "elapsed_seconds": int(elapsed),
            "from_status": observed,
        },
    )
    metrics.observe_run_outcome(status=RunStatus.FAILED.value)
    logger.info(
        "run timed out",
        run_id=run.id,
        account_id=account_id,
        timeout_seconds=timeout,
        elapsed_seconds=int(elapsed),
        from_status=observed,
        cancelled_jobs=cancelled_jobs,
    )
    return True


async def sweeper_loop(container: AppContainer) -> None:
    interval = max(container.settings.run_timeout_sweep_interval_seconds, 0.1)
    while True:
        try:
            await sweep_once(container)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("run timeout sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-run-timeouts"
    )
