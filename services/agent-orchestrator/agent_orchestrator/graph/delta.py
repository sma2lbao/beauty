"""任务执行的正文增量通道:节点上报、services 层落库(架构 §6.3)。

分层约定(§4)不变:节点仍是纯函数——只读 state、返回增量,不碰数据库、
不发事件。节点用注入到本次图调用的 :class:`DeltaProjector` 上报正文,真正的
落库与分发在 services 层的图调用边界完成。

未注入 sink(单测直跑节点、无 worker 的场景)时上报静默丢弃,节点行为因此与
"不流式"完全一致。进程级 ``configure`` 仅剩**静态配置兼容层**(main.py 在
create_app 里按 settings 固定一次),sink 本身不再走进程级上下文变量。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_orchestrator.graph.ports import DeltaSink

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


@dataclass(frozen=True, slots=True)
class DeltaProjector:
    """一次图调用的正文增量投影器:显式持有 sink,无 sink 时静默 no-op。"""

    sink: DeltaSink | None = None

    def active(self) -> bool:
        """本次图调用是否有增量接收方(节点据此决定走流式还是普通调用)。"""
        return self.sink is not None

    async def emit_text(self, text: str, phase: str) -> None:
        """上报一段正文增量:无 sink 或空串时 no-op(绝不改变执行结果)。"""
        if self.sink is not None and text:
            await self.sink.push(text, phase)


@dataclass(frozen=True, slots=True)
class _StreamingConfig:
    """进程级静态配置(整对象替换,不做逐字段全局赋值)。"""

    enabled: bool = False
    flush_chars: int = DEFAULT_FLUSH_CHARS
    flush_seconds: float = DEFAULT_FLUSH_SECONDS


_config = _StreamingConfig()


def configure(
    *,
    enabled: bool,
    flush_chars: int = DEFAULT_FLUSH_CHARS,
    flush_seconds: float = DEFAULT_FLUSH_SECONDS,
) -> None:
    """进程级静态配置(create_app 按 settings 调一次;未配置即关闭)。

    TODO(R-29): main.py 的装配迁到显式注入后删除本兼容层。
    """
    global _config
    _config = _StreamingConfig(
        enabled=enabled,
        flush_chars=max(1, flush_chars),
        flush_seconds=max(0.0, flush_seconds),
    )


def enabled() -> bool:
    """正文增量通道是否启用(services 层的图调用边界据此决定建不建 sink)。"""
    return _config.enabled


def flush_chars() -> int:
    return _config.flush_chars


def flush_seconds() -> float:
    return _config.flush_seconds
