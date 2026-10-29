"""子代理的内联执行、终态轮询与取消收尾(自 subagents.py 拆出)。

``jobs_enabled=False`` 语义:子 run 在本进程内联跑,每 ``POLL_SECONDS`` 轮询
一次「子 run 终态 / 子 run 被取消 / 父 run 被取消 / 超时」。轮询与取消都开
独立会话,不碰父 run 的执行会话。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from database.session import session_scope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_execution import _invoke_plan
from agent_orchestrator.services.subagents_spec import (
    _ABORT_CANCELLED,
    _ABORT_PARENT,
    _ABORT_TIMEOUT,
    _TERMINAL_STATUSES,
    CANCELLED_REASON,
    PARENT_CANCELLED_REASON,
    POLL_SECONDS,
    TIMEOUT_REASON,
)

logger = logging.getLogger(__name__)


async def _execute_child(
    factory: async_sessionmaker[AsyncSession],
    child_id: str,
    *,
    run_graph: Any,
    store: Any,
    workspace: Any,
    downloader: Any,
    context: Any,
    settings: Settings,
) -> None:
    """在独立会话里跑子 run 的 plan 语义(``jobs_enabled=False``,不进队列)。"""

    async with session_scope(factory) as session:
        child = await session.get(Run, child_id)
        if child is None:
            logger.warning("子 run 不存在,跳过执行(child_id=%s)", child_id)
            return
        await _invoke_plan(
            session,
            run_graph,
            child,
            store=store,
            workspace=workspace,
            downloader=downloader,
            settings=settings,
            context=context,
        )


async def _wait_child(
    factory: async_sessionmaker[AsyncSession],
    *,
    parent_id: str,
    child_id: str,
    task: asyncio.Task[None],
    timeout_seconds: float,
) -> str | None:
    """每秒轮询子 run 终态/取消与父 run 取消;超时返回 abort 原因(§6)。

    完成判定用 ``task.done()`` 而不是 DB 状态:子 run 的终态写在子执行会话里,
    只在会话退出时提交——轮询 DB 看不到"刚结束"的瞬间。
    """

    deadline = time.monotonic() + timeout_seconds
    while True:
        done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
        if task in done:
            return None
        parent_cancelled, child_cancelled = await _poll_cancelled(
            factory, parent_id=parent_id, child_id=child_id
        )
        if parent_cancelled:
            return _ABORT_PARENT
        if child_cancelled:
            return _ABORT_CANCELLED
        if time.monotonic() >= deadline:
            return _ABORT_TIMEOUT


async def _poll_cancelled(
    factory: async_sessionmaker[AsyncSession], *, parent_id: str, child_id: str
) -> tuple[bool, bool]:
    """只读 status 列判取消(用独立会话,不碰父 run 执行会话)。"""

    async with session_scope(factory) as session:
        parent_status = await session.scalar(
            select(Run.status).where(Run.id == parent_id)
        )
        child_status = await session.scalar(
            select(Run.status).where(Run.id == child_id)
        )
    cancelled = RunStatus.CANCELLED.value
    return parent_status == cancelled, child_status == cancelled


async def _abort_child(task: asyncio.Task[None]) -> None:
    """取消子任务并吞掉取消/异常:子 run 的收尾由调用方按 DB 状态决定。"""

    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - 子 run 自己的异常已由 _fail_run 落库
        logger.warning("子 run 执行任务异常结束", exc_info=True)


async def _cancel_child(
    factory: async_sessionmaker[AsyncSession], child_id: str, reason: str
) -> Run | None:
    """把子 run 落成 cancelled(超时/父取消/外部取消共用,§6)。"""

    async with session_scope(factory) as session:
        child = await session.get(Run, child_id)
        if child is None:
            return None
        if child.status in _TERMINAL_STATUSES:
            return child
        child.status = RunStatus.CANCELLED.value
        child.status_reason = reason[:500]
        await append_event(session, child, "run.cancelled.v1", {"reason": reason})
        return child


def _cancel_text(abort: str) -> str:
    if abort == _ABORT_TIMEOUT:
        return TIMEOUT_REASON
    if abort == _ABORT_PARENT:
        return PARENT_CANCELLED_REASON
    return CANCELLED_REASON
