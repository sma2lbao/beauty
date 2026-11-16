"""任务执行的正文增量通道:节点上报、services 层落库(架构 §6.3)。

分层约定(§4)不变:节点仍是纯函数——只读 state、返回增量,不碰数据库、
不发事件。节点用注入到本次图调用的 :class:`DeltaProjector` 上报正文,真正的
落库与分发在 services 层的图调用边界完成。

未注入 sink(单测直跑节点、无 worker 的场景)时上报静默丢弃,节点行为因此与
"不流式"完全一致。开关与攒批口径不落在本模块:services 层的图调用边界按启动配置
(Settings)构造 sink,本模块只保留常量与投影器——进程级 ``configure`` 兼容层已删除
(R-29)。
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
