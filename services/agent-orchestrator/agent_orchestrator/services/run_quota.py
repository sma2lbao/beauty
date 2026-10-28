"""run 配额与用量边界(P0-1/P0-2):准入闸 fail-open,用量只做观测。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.errors import QuotaExceededError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import RunRepository
from agent_orchestrator.services import quota as quota_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services import usage_events

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _usage_boundary(session: AsyncSession, run: Run) -> AsyncIterator[None]:
    """图调用边界:绑定用量采集器,退出时上报 account 并落 run.usage.v1(P0-1/P1-E)。

    上报/落事件失败只打 warning,绝不影响 run——用量是观测数据,不是执行路径。
    事件同步写在本会话里(record_outbox=False):调用方随后 commit,用量随 run
    终态一起可见;无采样则不落事件。
    """
    collector = usage_service.UsageCollector()
    token = usage_service.bind(collector)
    try:
        yield
    finally:
        usage_service.unbind(token)
        await usage_service.report(
            tenant_id=run.tenant_id,
            run_id=run.id,
            samples=collector.samples,
        )
        await _record_usage(session, run, collector)


async def _record_usage(
    session: AsyncSession, run: Run, collector: usage_service.UsageCollector
) -> None:
    """把本次图调用的用量落成事件(空采样跳过;写失败不影响 run)。"""
    if not collector.samples:
        return
    try:
        from agent_orchestrator.services.run_events import append_event

        await append_event(
            session,
            run,
            usage_events.USAGE_EVENT,
            usage_events.usage_payload(collector.samples),
            record_outbox=False,
        )
    except Exception:  # noqa: BLE001 - 观测数据,任何异常都不能打断 run
        logger.warning("写入 run.usage.v1 失败(run_id=%s)", run.id, exc_info=True)


async def _enforce_quota(session: AsyncSession, tenant_id: str) -> None:
    """P0-2 准入闸:月度 token 用尽 / 并发 run 超限 → 429。

    快照取不到(=account 未配置或不可用)时不拦:配额是保护性限流,
    不能被观测依赖的抖动连带打死。判定语义:
    - 月度:``used_tokens >= monthly_tokens`` 即拒绝(边界值按用尽算);
    - 并发:未终结 run 数(排除 completed/failed/blocked)``>= max_concurrent_runs``。
    """
    snapshot = await quota_service.fetch(tenant_id)
    if snapshot is None:
        return
    if snapshot.monthly_tokens_exceeded:
        raise QuotaExceededError(
            "quota.monthly_tokens_exceeded",
            "本月模型用量已超出配额,请升级或等待下月重置",
            details={
                "limit": snapshot.monthly_tokens,
                "used": snapshot.used_tokens,
            },
        )
    limit = snapshot.max_concurrent_runs
    if limit is not None:
        active = await RunRepository(session).count_active(tenant_id)
        if active >= limit:
            raise QuotaExceededError(
                "quota.concurrent_runs_exceeded",
                "同时进行的任务数已达上限,请等待现有任务结束",
                details={"limit": limit, "active": active},
            )
