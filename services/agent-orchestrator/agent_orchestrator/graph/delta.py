"""任务执行的正文增量通道:节点上报、services 层落库(架构 §6.3)。

分层约定(§4)保持不变:节点仍是纯函数——只读 state、返回增量,不碰数据库、
不发事件。但节点知道"自己正在写什么",所以这里只给一条**上报**通道:
节点调 :func:`emit_text`,真正的落库与分发在 services 层的图调用边界完成。

未绑定 sink(单测直跑节点、无 worker 的场景)时 :func:`emit_text` 静默丢弃,
节点行为因此与"不流式"完全一致。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Protocol

#: 事件 payload 的 phase:前端据此分组显示(同一阶段拼一段,换阶段即换段)
PHASE_PLAN = "plan"
PHASE_EXECUTE = "execute"
PHASE_VERIFY = "verify"
PHASE_DELIVER = "deliver"

#: 事件类型(§6.3 命名约定:run.started.v1 / token.delta.v1 / …)
EVENT_TYPE = "token.delta.v1"

#: 默认攒批口径:240 字或 0.25 秒先到者提交一次(SSE 0.5s 轮询下约每秒 2 帧)
DEFAULT_FLUSH_CHARS = 240
DEFAULT_FLUSH_SECONDS = 0.25


class DeltaSink(Protocol):
    """增量接收方(services 层实现):按批落成 run 事件。"""

    async def push(self, text: str, phase: str) -> None: ...


_sink: ContextVar[DeltaSink | None] = ContextVar("run_delta_sink", default=None)
_enabled: bool = False
_flush_chars: int = DEFAULT_FLUSH_CHARS
_flush_seconds: float = DEFAULT_FLUSH_SECONDS


def configure(
    *,
    enabled: bool,
    flush_chars: int = DEFAULT_FLUSH_CHARS,
    flush_seconds: float = DEFAULT_FLUSH_SECONDS,
) -> None:
    """进程级配置(create_app 按 settings 调一次;未配置即关闭)。"""
    global _enabled, _flush_chars, _flush_seconds
    _enabled = enabled
    _flush_chars = max(1, flush_chars)
    _flush_seconds = max(0.0, flush_seconds)


def enabled() -> bool:
    return _enabled


def flush_chars() -> int:
    return _flush_chars


def flush_seconds() -> float:
    return _flush_seconds


def active() -> bool:
    """当前协程上下文里是否有 sink(节点据此决定走流式还是普通调用)。"""
    return _sink.get() is not None


def bind(sink: DeltaSink) -> Token:
    return _sink.set(sink)


def unbind(token: Token) -> None:
    _sink.reset(token)


async def emit_text(text: str, phase: str) -> None:
    """节点上报正文增量:无 sink 或空串时 no-op(绝不因此改变执行结果)。"""
    sink = _sink.get()
    if sink is not None and text:
        await sink.push(text, phase)
