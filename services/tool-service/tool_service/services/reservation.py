"""执行期预留:running 行、租约抢占、取消信号与终态结算。

为什么需要它(S6):tool_calls 以前只在执行结束后落一行,服务端唯一约束只能
在「提交期」兜并发——同键请求在「执行已经发生、审计行还没落库」的窗口里仍会
各执行一次。现在先插一行 status=running 的预留行并带租约,第二次同键请求
立刻 409 tool.call_in_progress;执行方崩溃留下的 running 行在租约过期后允许
被别的请求抢占重试(进程内没有后台 reaper,只靠下一次同键调用触发)。

取消同理:orchestrator 取消 run 时把在飞的 running 行置 cancel_requested,
执行方在轮询间隔内放弃等待。已经发到沙箱的命令不会被打断(本服务只是不再等
它的结果),想彻底停掉要用 job_kill / 沙箱生命周期。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tool_service.models.tool_call import (
    STATUS_RUNNING,
    TERMINAL_STATUSES,
    ToolCall,
)

T = TypeVar("T")

#: 在飞调用被取消时回给调用方的错误码/文案(模型可见的工具错误,HTTP 200)。
CANCELLED_CODE = "tool.cancelled"
CANCELLED_MESSAGE = "该工具调用已被取消,执行方不再等待结果"
#: 同键调用仍在执行时的 API 错误码(409)。
IN_PROGRESS_CODE = "tool.call_in_progress"
IN_PROGRESS_MESSAGE = "同一幂等键的工具调用正在执行,请稍后重试"

#: 轮询取消标记的默认间隔(秒);由 settings.cancel_poll_seconds 覆盖。
DEFAULT_POLL_SECONDS = 0.5


class ToolCallCancelled(Exception):
    """执行方轮询到取消请求:调用方应结算成 cancelled 并回工具错误信封。"""


def lease_deadline(seconds: float, *, now: datetime | None = None) -> datetime:
    """租约到期时间;下限 1 秒,避免配成 0 后立刻可被抢占。"""

    return (now or datetime.now(UTC)) + timedelta(seconds=max(seconds, 1.0))


async def takeover(
    session: AsyncSession, record_id: str, *, lease_until: datetime
) -> bool:
    """抢占租约过期的 running 行(单条 CAS);抢到 True,被别人先抢到 False。

    用 SQL 比较而不是 Python 比较时间:SQLite 会把 tz 丢掉(读回来是 naive),
    PG 读回 aware,两边在 Python 里比较会炸;交给数据库比反而两边一致。
    """

    now = datetime.now(UTC)
    result = await session.execute(
        update(ToolCall)
        .where(
            ToolCall.id == record_id,
            ToolCall.status == STATUS_RUNNING,
            or_(ToolCall.lease_until.is_(None), ToolCall.lease_until < now),
        )
        .values(
            status=STATUS_RUNNING,
            lease_until=lease_until,
            cancel_requested=False,
            ok=False,
            error_code=None,
            duration_ms=0,
            result_json=None,
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return int(result.rowcount or 0) == 1


async def cancel_requested(session: AsyncSession, record_id: str) -> bool:
    """读一行的取消标记;每次自成一个短事务,避免长读事务挡住取消方的写。"""

    value = await session.scalar(
        select(ToolCall.cancel_requested).where(ToolCall.id == record_id)
    )
    await session.commit()
    return bool(value)


async def request_cancel(
    session: AsyncSession,
    *,
    account_id: str,
    run_id: str | None = None,
    tool_call_id: str | None = None,
) -> int:
    """把在飞调用的取消标记置位,返回影响行数(账号内,至少给一个过滤条件)。"""

    if run_id is None and tool_call_id is None:
        raise ValueError("按 run_id 或 tool_call_id 取消,二者至少给一个")
    conditions = [ToolCall.account_id == account_id, ToolCall.status == STATUS_RUNNING]
    if run_id is not None:
        conditions.append(ToolCall.run_id == run_id)
    if tool_call_id is not None:
        conditions.append(ToolCall.tool_call_id == tool_call_id)
    result = await session.execute(
        update(ToolCall)
        .where(*conditions)
        .values(cancel_requested=True)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return int(result.rowcount or 0)


async def settle(
    session: AsyncSession,
    record_id: str,
    *,
    status: str,
    columns: dict[str, Any],
    result_json: str | None = None,
) -> None:
    """把预留行结算成终态;同时清租约与取消标记(终态行不再会被抢占)。"""

    if status not in TERMINAL_STATUSES:
        raise ValueError(f"结算状态必须是终态,收到 {status!r}")
    values: dict[str, Any] = dict(columns)
    values.update(
        status=status,
        lease_until=None,
        cancel_requested=False,
        result_json=result_json,
    )
    await session.execute(
        update(ToolCall)
        .where(ToolCall.id == record_id)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def await_with_cancel(
    start: Callable[[], Awaitable[T]],
    *,
    probe: Callable[[], Awaitable[bool]],
    poll_interval: float = DEFAULT_POLL_SECONDS,
) -> T:
    """跑 start(),每 poll_interval 秒问一次 probe();探到取消就放弃等待。

    start 传「零参可调用」而不是现成的协程,否则取消路径会留下一个从未被
    await 的协程告警。放弃等待时对被包住的 task 调 cancel(),把它内部的
    asyncio 等待(HTTP 调用/沙箱 exec)一起取消;线程里的阻塞调用无法打断,
    那种情况只能等它自己结束。
    """

    task: asyncio.Task[T] = asyncio.ensure_future(start())
    interval = max(poll_interval, 0.01)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                return task.result()
            if await probe():
                raise ToolCallCancelled(CANCELLED_MESSAGE)
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        raise
