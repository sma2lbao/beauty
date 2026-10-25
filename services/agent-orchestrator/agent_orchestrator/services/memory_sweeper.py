"""记忆周期清理与巩固 sweeper(P2-1)。

为什么需要:过期记忆(purge_expired)与近重复巩固(consolidate)如果只靠人工
调接口,生产库会越积越脏。本 sweeper 是后台任务:按固定间隔遍历"有生效记忆的
租户",先清理过期、再合并近重复;单租户失败只记日志,不影响其他租户。

启动条件:interval <= 0 时循环直接返回(测试与本地默认关闭);容器负责建任务、
stop 时取消。与 interrupts sweeper 的区别是它按租户遍历而不是扫单条记录。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.repositories import MemoryRepository
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.memory_extraction import consolidate

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)


async def sweep_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    consolidation_enabled: bool = True,
    threshold: float = 0.92,
) -> dict[str, int]:
    """扫一轮:逐租户清理过期 + (可选)合并近重复,返回汇总计数。

    先单独取租户清单再逐租户开事务:单租户处理失败不会回滚其他租户,也让每轮
    事务范围尽量小(后台任务与在线请求抢锁的概率更低)。
    """
    async with session_scope(session_factory) as session:
        tenant_ids = await MemoryRepository(session).tenant_ids_with_active()

    totals = {"tenants": 0, "purged": 0, "consolidated": 0}
    for tenant_id in tenant_ids:
        totals["tenants"] += 1
        try:
            async with session_scope(session_factory) as session:
                totals["purged"] += await memory_service.purge_expired(
                    session, tenant_id
                )
                if consolidation_enabled:
                    totals["consolidated"] += await consolidate(
                        session, tenant_id, threshold=threshold
                    )
        except Exception as exc:  # noqa: BLE001 - 单租户失败不能中断整轮
            logger.warning("memory sweep failed", tenant_id=tenant_id, error=str(exc))
    return totals


async def sweeper_loop(container: AppContainer) -> None:
    """周期执行 sweep_once;间隔 <= 0 或没有数据库时直接退出(不算错误)。"""
    interval = float(container.settings.memory_sweep_interval_seconds)
    if interval <= 0 or container.session_factory is None:
        return
    settings = container.settings
    while True:
        try:
            await sweep_once(
                container.session_factory,
                consolidation_enabled=settings.memory_consolidation_enabled,
                threshold=settings.memory_consolidation_threshold,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下一轮继续
            logger.warning("memory sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-memory-sweeper"
    )
