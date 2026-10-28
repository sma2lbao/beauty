"""图节点步骤采集(07-run-step-model.md S1)。

节点是纯函数、不落库:这里只记录每个节点的起止与终态(span),由 services 层
在每次 ``graph.ainvoke`` 边界统一落成 ``run.step.v1`` 事件——步骤身份与时间线
是事件投影(``GET /api/v1/runs/{id}/steps``),不改表、不动迁移。
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

#: 步骤事件类型:payload 是 StepSpan 的固化结构;对外步骤视图见 services.run_steps。
EVENT_TYPE = "run.step.v1"


class StepSink(Protocol):
    """span 接收端(services 层实现为内存收集器,调用边界统一落库)。"""

    def push(self, span: StepSpan) -> None: ...


@dataclass(slots=True)
class StepSpan:
    """一个节点的执行片段:节点名 + 阶段 + 步骤身份 + 起止时间 + 终态。"""

    node: str
    phase: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    #: 步骤身份 ``<run_id>:<attempt>:<node>:<round>``(item 的 step_id 锚点)。
    step_id: str | None = None
    attempt: int = 0
    round: int = 0

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


class StepProjector:
    """一次图调用的步骤采集器:没有 sink(直跑节点的单测)时全部 no-op。

    ``begin`` 即分配 step_id(``<run_id>:<attempt>:<node>:<round>``):round 由构造
    时预读的 ``(attempt, node)`` 历史计数推进,同一 run 重跑/多轮同名节点不撞号;
    ``current_step_id`` 供 item 上报挂到当前步骤(契约 §7)。
    """

    def __init__(
        self,
        sink: StepSink | None = None,
        *,
        run_id: str | None = None,
        attempt: int = 0,
        rounds: Mapping[str, int] | None = None,
    ) -> None:
        self._sink = sink
        self._run_id = run_id
        self._attempt = attempt
        self._rounds: dict[str, int] = dict(rounds or {})
        self._current: str | None = None

    def active(self) -> bool:
        return self._sink is not None

    def current_step_id(self) -> str | None:
        """最近开始的步骤 id(尚未 begin 或没有 sink 时为 None)。"""

        return self._current

    def begin(self, node: str, phase: str) -> StepSpan | None:
        if self._sink is None:
            return None
        round_no = self._rounds.get(node, 0) + 1
        self._rounds[node] = round_no
        step_id = None
        if self._run_id is not None:
            step_id = f"{self._run_id}:{self._attempt}:{node}:{round_no}"
        self._current = step_id
        return StepSpan(
            node=node,
            phase=phase,
            started_at=datetime.now(UTC),
            status="running",
            step_id=step_id,
            attempt=self._attempt,
            round=round_no,
        )

    def finish(self, span: StepSpan | None, status: str = "completed") -> None:
        if self._sink is None or span is None:
            return
        span.finished_at = datetime.now(UTC)
        span.status = status
        self._sink.push(span)


def _result_status(result: Any) -> str:
    """节点结果里的终态:只有 fail_node 会把 final_status 标成 failed/blocked。"""
    if not isinstance(result, dict):
        return "completed"
    final = result.get("final_status")
    if final in {"failed", "blocked"}:
        return str(final)
    return "completed"


def instrument_node(
    name: str, phase: str, node: Callable[..., Any]
) -> Callable[..., Any]:
    """包装异步节点:记录本节点 span 交给本次调用的 StepProjector。

    包装层只读 invocation config 里的 GraphRuntime(与端口/增量同一注入方式),
    不改节点内部:节点仍是纯函数,失败/中断的终态如实标在 span 上。
    """

    @functools.wraps(node)
    async def wrapper(state: Any, config: Any = None) -> Any:
        # 局部 import:graph.runtime 依赖本模块的 StepProjector,顶层互引会成环
        from agent_orchestrator.graph.runtime import runtime_from_config

        projector = runtime_from_config(config).steps
        span = projector.begin(name, phase)
        try:
            result = await node(state, config)
        except BaseException:
            projector.finish(span, "failed")
            raise
        projector.finish(span, _result_status(result))
        return result

    return wrapper
