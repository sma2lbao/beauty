"""24h 刷新巡检:兜住「没人调用执行面」的长期空闲连接(设计 §5.5)。

与惰性刷新/手动刷新共用 connections.refresh_connection 的单飞路径(行锁 + 锁内
二次检查),所以巡检和执行面并发时不会重复换 token(某些 provider 的 refresh_token
只能使用一次)。

巡检是跨账号的系统任务:先一次性取出候选(只快照 account_id/id),再逐条在**独立
session_scope** 里刷新——单条失败只留痕(错误码/状态)并记日志,不影响整轮。
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import httpx
from common.logging import get_logger
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from connector.core.clock import utcnow
from connector.core.config import Settings
from connector.providers.errors import ProviderError
from connector.repositories import ConnectionRepository
from connector.services.connections import refresh_connection

_log = get_logger("connector.patrol")


async def run_patrol_once(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
) -> int:
    """跑一轮巡检,返回本轮处理的连接数(不含候选为 0 时)。

    失败隔离:单条连接抛 ProviderError 时 _refresh_locked 已把 last_error_code
    (必要时 status=expired)写进行内,这里必须提交再继续,否则 session_scope 的
    rollback 会把失败留痕一起回滚;未知异常只记日志,继续下一条。
    """
    cutoff = utcnow() - timedelta(seconds=settings.refresh_stale_seconds)
    async with session_scope(session_factory) as session:
        stale = await ConnectionRepository(session).list_stale_active(cutoff)
        targets = [(conn.account_id, conn.id) for conn in stale]
    if not targets:
        return 0

    processed = 0
    for account_id, connection_id in targets:
        processed += 1
        try:
            async with session_scope(session_factory) as session:
                try:
                    await refresh_connection(
                        session, settings, account_id, connection_id, transport
                    )
                except ProviderError as exc:
                    await session.commit()  # 失败留痕要落库
                    _log.warning(
                        "patrol_refresh_failed",
                        account_id=account_id,
                        connection_id=connection_id,
                        error_code=exc.code,
                    )
        except Exception as exc:  # 单条异常不能杀死整轮(含锁冲突/DB 抖动)
            _log.warning(
                "patrol_connection_failed",
                account_id=account_id,
                connection_id=connection_id,
                error=f"{type(exc).__name__}: {exc}",
            )
    _log.info("patrol_round_finished", processed=processed)
    return processed


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None,
    stop: asyncio.Event,
) -> None:
    """后台循环:先等一个间隔再跑(启动瞬间不去抢执行面的行锁),可被 stop 唤醒。"""
    interval = max(1.0, float(settings.refresh_patrol_interval_seconds))
    while not stop.is_set():
        # 正常到期(超时)不算错误;stop 被 set 时 wait_for 立刻返回
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        try:
            await run_patrol_once(session_factory, settings, transport)
        except Exception as exc:  # 整轮崩溃(如 DB 不可用)也不能杀死任务
            _log.warning("patrol_round_crashed", error=f"{type(exc).__name__}: {exc}")
