"""记忆周期清理与巩固 sweeper(P2-1)。

为什么需要:过期记忆(purge_expired)与近重复巩固(consolidate)如果只靠人工
调接口,生产库会越积越脏。本 sweeper 是后台任务:按固定间隔遍历"有生效记忆的
账号",先清理过期、再合并近重复;单账号失败只记日志,不影响其他账号。

多副本安全:同一时刻只允许一个实例真正清扫。Postgres 走会话级 advisory lock
(pg_try_advisory_lock),拿不到锁直接跳过本轮;SQLite/其他库退化为进程内
asyncio.Lock(仅覆盖同进程,测试与单副本本地部署足够)。跳过与完成都写结构化
审计日志并打 Prometheus 指标(见 agent_orchestrator.metrics)。

启动条件:interval <= 0 时循环直接返回(测试与本地默认关闭);容器负责建任务、
stop 时取消。与 interrupts sweeper 的区别是它按账号遍历而不是扫单条记录。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from database.session import session_scope
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from agent_orchestrator.metrics import observe_memory_sweep
from agent_orchestrator.repositories import MemoryRepository
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.memory_extraction import consolidate

if TYPE_CHECKING:
    from agent_orchestrator.core.container import AppContainer

logger = structlog.get_logger(__name__)

# 会话级 advisory lock 的固定键("MEMS");换键等于换锁,不要随手改。
_MEMORY_SWEEP_LOCK_KEY = 0x4D454D53

_process_lock: asyncio.Lock | None = None
_process_lock_loop: asyncio.AbstractEventLoop | None = None


def _process_guard() -> asyncio.Lock:
    """取当前事件循环对应的进程内锁(测试/本地 SQLite 退路)。

    asyncio.Lock 绑定事件循环,跨测试重建循环时必须换新锁,否则会复用已关闭
    循环上的原语。
    """
    global _process_lock, _process_lock_loop
    loop = asyncio.get_running_loop()
    if _process_lock is None or _process_lock_loop is not loop:
        _process_lock = asyncio.Lock()
        _process_lock_loop = loop
    return _process_lock


def _backend_name(session: AsyncSession) -> str:
    dialect = getattr(session.get_bind(), "dialect", None)
    return getattr(dialect, "name", "unknown")


def _lock_engine(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncEngine | None:
    """取 session 工厂背后的 engine(create_session_factory 会挂 .engine)。"""
    engine = getattr(session_factory, "engine", None)
    if engine is not None:
        return engine
    kw = getattr(session_factory, "kw", None) or {}
    return kw.get("bind")


async def _release_lock(connection: AsyncConnection) -> None:
    """在同一连接上释放锁;连接关闭时也会自动释放(引擎使用 NullPool)。"""
    try:
        unlocked = bool(
            await connection.scalar(
                select(func.pg_advisory_unlock(_MEMORY_SWEEP_LOCK_KEY))
            )
        )
        await connection.commit()
    except Exception as exc:  # noqa: BLE001 - 释放失败不能让清扫结果丢失
        logger.warning("memory sweep unlock failed", error=str(exc))
        return
    if not unlocked:
        logger.warning("memory sweep lock not held on release")


@asynccontextmanager
async def _advisory_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """在独占连接上拿 Postgres 会话级锁,并在整个清扫期间保持该连接。

    为什么不用普通 AsyncSession:会话级锁绑定在数据库连接上,而 session
    提交/回滚后连接可能归还连接池,后续 unlock 会落到另一条连接。这里用
    engine.connect() 独占一条连接,拿到锁后提交(锁随连接保留),清扫结束
    再在同一连接上解锁并关闭。
    """
    engine = _lock_engine(session_factory)
    if engine is None:
        raise RuntimeError(
            "memory sweeper 需要 session_factory.engine 才能获取 advisory lock"
        )
    async with engine.connect() as connection:
        acquired = bool(
            await connection.scalar(
                select(func.pg_try_advisory_lock(_MEMORY_SWEEP_LOCK_KEY))
            )
        )
        if acquired:
            await connection.commit()
        else:
            await connection.rollback()
        try:
            yield acquired
        finally:
            if acquired:
                await _release_lock(connection)


async def _sweep_accounts(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    consolidation_enabled: bool,
    threshold: float,
) -> tuple[dict[str, int], int]:
    """逐账号清理过期 + (可选)合并近重复,返回(汇总计数, 失败账号数)。"""
    async with session_scope(session_factory) as session:
        account_ids = await MemoryRepository(session).account_ids_with_active()

    totals = {"accounts": 0, "purged": 0, "consolidated": 0}
    failed = 0
    for account_id in account_ids:
        totals["accounts"] += 1
        try:
            async with session_scope(session_factory) as session:
                totals["purged"] += await memory_service.purge_expired(
                    session, account_id
                )
                if consolidation_enabled:
                    totals["consolidated"] += await consolidate(
                        session, account_id, threshold=threshold
                    )
        except Exception as exc:  # noqa: BLE001 - 单账号失败不能中断整轮
            failed += 1
            logger.warning("memory sweep failed", account_id=account_id, error=str(exc))
    return totals, failed


def _skipped() -> dict[str, int]:
    return {"accounts": 0, "purged": 0, "consolidated": 0, "skipped": 1}


def _log_skipped(backend: str) -> dict[str, int]:
    logger.info("memory sweep skipped", backend=backend, reason="lock_held")
    observe_memory_sweep(outcome="skipped")
    return _skipped()


async def sweep_once(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    consolidation_enabled: bool = True,
    threshold: float = 0.92,
) -> dict[str, int]:
    """扫一轮:逐账号清理过期 + (可选)合并近重复,返回汇总计数。

    先单独取账号清单再逐账号开事务:单账号处理失败不会回滚其他账号,也让每轮
    事务范围尽量小(后台任务与在线请求抢锁的概率更低)。返回中的 skipped=1
    表示本轮锁被其他实例持有、没有执行清扫。
    """
    async with session_factory() as probe:
        backend = _backend_name(probe)

    if backend == "postgresql":
        async with _advisory_lock(session_factory) as acquired:
            if not acquired:
                return _log_skipped(backend)
            totals, failed = await _sweep_accounts(
                session_factory,
                consolidation_enabled=consolidation_enabled,
                threshold=threshold,
            )
    else:
        guard = _process_guard()
        if guard.locked():
            return _log_skipped(backend)
        async with guard:
            totals, failed = await _sweep_accounts(
                session_factory,
                consolidation_enabled=consolidation_enabled,
                threshold=threshold,
            )

    observe_memory_sweep(
        outcome="completed",
        purged=totals["purged"],
        consolidated=totals["consolidated"],
        failed_accounts=failed,
    )
    logger.info(
        "memory sweep completed",
        backend=backend,
        accounts=totals["accounts"],
        purged=totals["purged"],
        consolidated=totals["consolidated"],
        failed_accounts=failed,
        skipped=0,
    )
    totals["skipped"] = 0
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
            observe_memory_sweep(outcome="failed")
            logger.warning("memory sweep iteration failed", error=str(exc))
        await asyncio.sleep(interval)


def start_sweeper(container: AppContainer) -> asyncio.Task[None]:
    """作为后台任务启动;调用方(容器)负责在 stop 时取消。"""
    return asyncio.create_task(
        sweeper_loop(container), name="orchestrator-memory-sweeper"
    )
