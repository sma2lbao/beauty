"""中断超时 sweeper(P0-2):待审批/待确认停留过久时自动按"拒绝"收场。

为什么需要:审批与危险操作确认都是**无限期等待人工**——worker 崩溃、用户离线
或前端没渲染出按钮,run 会永远停在 awaiting_*。本 sweeper 是后台任务,只在
jobs_enabled 且至少一类超时 > 0 时启动(见 AppContainer._assemble_graph)。

收场方式复用既有动作而不是直接改状态,保证状态机与业务语义不变:
- 审批超时 → 入队 reject job → run 走 reject 语义(blocked + 原因,可 resume);
- 确认超时 → 入队 confirm(拒绝)job → 跳过危险写,图继续验收(§6.2 拒绝语义)。
先记 run.interrupt.timed_out.v1 再入队,时间线可审计。

多实例注意:多容器同时扫描时 enqueue 对同 run 同 kind 去重,但 timed_out 事件
可能重复一两次(updated_at 推进后下一轮不再命中);彻底幂等属 P0-3。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.repositories import RunRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.jobs import enqueue
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_lifecycle import require_run

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)

APPROVAL_TIMEOUT_REASON = "审批超时,自动拒绝(可 resume 后重新规划)"
CONFIRMATION_TIMEOUT_REASON = "确认超时,自动拒绝危险写并继续验收"
TIMED_OUT_EVENT = "run.interrupt.timeout.v1"


async def sweep_once(container: AppContainer) -> int:
    """扫一轮超时中断,返回实际处理条数;单条失败只记日志,不中断整轮。"""
    settings = container.settings
    factory = container.session_factory
    if not settings.jobs_enabled or factory is None:
        return 0
    async with session_scope(factory) as session:
        stale = await RunRepository(session).list_stale_interrupts(
            datetime.now(UTC),
            approval_seconds=settings.approval_timeout_seconds,
            confirmation_seconds=settings.confirmation_timeout_seconds,
        )
        targets = [(run.tenant_id, run.id) for run in stale]
    handled = 0
    for tenant_id, run_id in targets:
        try:
            async with session_scope(factory) as session:
                if await _timeout_one(session, tenant_id, run_id, settings):
                    handled += 1
        except Exception as exc:  # noqa: BLE001 - 后台任务不能因单条失败退出
            logger.warning("interrupt sweep failed", run_id=run_id, error=str(exc))
    return handled


async def _timeout_one(
    session: AsyncSession,
    tenant_id: str,
    run_id: str,
    settings: Settings,
) -> bool:
    """复核状态并按类型收场;扫描后状态已变(用户刚处理)则返回 False。"""
    run = await require_run(session, tenant_id, run_id)
    if run.status == RunStatus.AWAITING_APPROVAL.value:
        kind = "approval"
        seconds = settings.approval_timeout_seconds
        job_kind = "reject"
        reason = APPROVAL_TIMEOUT_REASON
        payload = {"reason": reason}
    elif run.status == RunStatus.AWAITING_CONFIRMATION.value:
        kind = "confirmation"
        seconds = settings.confirmation_timeout_seconds
        job_kind = "confirm"
        reason = CONFIRMATION_TIMEOUT_REASON
        payload = {"action": "reject", "reason": reason}
    else:
        return False
    await append_event(
        session,
        run,
        TIMED_OUT_EVENT,
        {"kind": kind, "timeout_seconds": seconds, "reason": reason},
    )
    if kind == "confirmation":
        # 确认超时是"系统代替人做出的拒绝决定":worker 侧 confirm 分支
        # emit_confirmation=False(避免与 API 人工确认重复),这条审计记录
        # 只能由 sweeper 自己补,否则确认超时的事件树里没有任何决定痕迹。
        await append_event(
            session,
            run,
            "run.confirmation.received.v1",
            {"action": "reject", "reason": reason},
        )
    # 主动推进 updated_at:超时阈值从本轮处理重新起算,避免下一轮重复公告
    run.updated_at = datetime.now(UTC)
    await enqueue(session, tenant_id, job_kind, run.id, payload=payload)
    logger.info(
        "interrupt timed out",
        run_id=run.id,
        tenant_id=tenant_id,
        kind=kind,
        timeout_seconds=seconds,
    )
    return True


async def sweeper_loop(container: AppContainer) -> None:
    interval = max(container.settings.interrupt_sweep_interval_seconds, 0.1)
    while True:
        try:
            await sweep_once(container)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("interrupt sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(sweeper_loop(container), name="orchestrator-interrupts")
