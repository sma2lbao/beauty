"""协作式取消:节点边界问一次,执行期也能放弃等待(design/recovery.md §9)。

图节点是纯函数,拿不到会话,也不该自己抛"用户中断";取消的**权威信号**始终
是库里的 `run.status = cancelled`(见 services/run_cancel.py)。这里提供两个
手段:

- :func:`raise_if_cancelled`:节点在安全边界(节点入口、每次模型调用前、每次
  工具执行前)问一句"还该继续吗",答案是"已取消"就抛 :class:`RunCancelled`;
- :func:`guarded`:包住一次可能跑几十秒的外呼(模型补全、工具执行、流式增量),
  按 :data:`GUARD_POLL_SECONDS` 轮询探针,已取消就取消被包住的协程并抛
  :class:`RunCancelled`——这样长调用不用等到跑完才停(S6)。

边界语义不变:节点入口与副作用之前仍然先问一次;区别只在于"已经开始的这一次
外呼"可以被主动放弃等待。工具服务侧看到取消标记后也会放弃等待(S6),两边
不依赖同一条连接。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TypeVar

from agent_orchestrator.graph.runtime import GraphRuntime

T = TypeVar("T")

#: 执行期轮询取消探针的间隔;太小会放大查库次数,太大会托住取消的响应。
GUARD_POLL_SECONDS = 0.25

#: 取消时的异常文案(落日志/审计用,不落 run.status_reason——那是取消方写的)。
CANCELLED_MESSAGE = "run 已取消,执行在节点边界停止"


class RunCancelled(Exception):
    """图执行在节点边界发现 run 已被取消:向上抛出,由调用边界收尾。"""

    def __init__(self, run_id: str | None = None) -> None:
        super().__init__(CANCELLED_MESSAGE)
        self.run_id = run_id


async def raise_if_cancelled(runtime: GraphRuntime) -> None:
    """问一次取消探针;已取消则抛 RunCancelled。

    未注入探针时(:data:`DEFAULT_RUNTIME`)恒为"未取消",直跑节点的单测
    与不关心取消的调用方行为完全不变。
    """
    if await runtime.ports.cancel.is_cancelled():
        raise RunCancelled()


async def guarded(
    runtime: GraphRuntime,
    start: Callable[[], Awaitable[T]],
    *,
    poll_interval: float = GUARD_POLL_SECONDS,
) -> T:
    """执行期内也能被打断:跑 start(),每 poll_interval 秒问一次取消探针。

    start 传"零参可调用"而不是现成协程,取消路径才不会留下从未 await 的
    协程告警。探到已取消时对被包住的 task 调 cancel(),把内部 asyncio 等待
    (模型流、HTTP 请求)一起取消;线程里的阻塞调用无法打断,只能等它自己结束。
    """

    task: asyncio.Task[T] = asyncio.ensure_future(start())
    interval = max(poll_interval, 0.01)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                return task.result()
            if await runtime.ports.cancel.is_cancelled():
                raise RunCancelled()
    except BaseException:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        raise
