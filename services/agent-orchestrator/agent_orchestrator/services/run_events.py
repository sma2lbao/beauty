"""run 事件:写入(seq 单调)、正文增量攒批、回放查询与失败终态投影。

事件信封负责命名/版本校验(§6.3);正文增量只走 DB 回放,不进 outbox。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from common import metrics
from events.envelope import EventEnvelope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph import delta
from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.repositories import MessageRepository, RunEventRepository
from agent_orchestrator.schemas.runs import RunStatus


async def append_event(
    session: AsyncSession,
    run: Run,
    type_: str,
    payload: dict[str, Any],
    *,
    record_outbox: bool = True,
) -> RunEvent:
    """追加 run 事件:seq 每 run 单调递增;信封负责命名/版本校验(§6.3)。

    ``record_outbox=False`` 用于纯展示的瞬时事件(正文增量):它们只走
    DB 回放,不必再进 outbox 投递给外部消费方。
    """
    env = EventEnvelope.create(type_, payload)
    events = RunEventRepository(session)
    event = RunEvent(
        id=env.id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        seq=await events.next_seq(run.id),
        type=env.type,
        payload=env.payload,
        occurred_at=env.occurred_at,
    )
    await events.add(event)
    # 事务性 outbox(§6.3):与 run_events 同事务登记待投递事件;
    # recorder 由 API 层挂在 session.info 上(见 api/deps.get_session)
    recorder = session.info.get("outbox") if record_outbox else None
    if recorder is not None:
        await recorder(session, run, env)
    return event


class _RunDeltaSink:
    """把节点上报的正文增量攒成 ``token.delta.v1`` 事件(§6.3)。

    与图调用共用同一个 session 并逐批提交:每批因此有各自可见的 seq 时刻,
    SSE 回放能按序拼回整段正文,前端也能边收边渲染。攒批口径是"字数或时长
    先到者"(默认 240 字 / 0.25 秒)——逐字写库会把一次 run 变成成百上千行
    事件,轮询回放也扛不住。
    """

    def __init__(self, session: AsyncSession, run: Run) -> None:
        self._session = session
        self._run = run
        self._flush_chars = delta.flush_chars()
        self._flush_seconds = delta.flush_seconds()
        self._buffer: list[str] = []
        self._phase = delta.PHASE_EXECUTE
        self._chars = 0
        self._last_flush = time.monotonic()

    async def push(self, text: str, phase: str) -> None:
        if phase != self._phase and self._buffer:
            # 阶段切换先吐干净上一段:前端按 phase 分组,不能混在一段里
            await self._flush()
        self._phase = phase
        self._buffer.append(text)
        self._chars += len(text)
        if (
            self._chars >= self._flush_chars
            or time.monotonic() - self._last_flush >= self._flush_seconds
        ):
            await self._flush()

    async def close(self) -> None:
        await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        text = "".join(self._buffer)
        self._buffer.clear()
        self._chars = 0
        self._last_flush = time.monotonic()
        await append_event(
            self._session,
            self._run,
            delta.EVENT_TYPE,
            {"text": text, "phase": self._phase},
            record_outbox=False,
        )
        await self._session.commit()


@asynccontextmanager
async def _delta_stream(
    session: AsyncSession, run: Run
) -> AsyncIterator[delta.DeltaProjector]:
    """图调用边界:构造本次 run 的正文增量投影器(未开启时为空投影器)。

    投影器经 graph invocation config 显式注入节点(不再用 ContextVar 绑定),
    图内节点只上报、落库与提交仍在这里完成。
    """
    if not delta.enabled():
        yield delta.DeltaProjector()
        return
    sink = _RunDeltaSink(session, run)
    # 开跑前的写入(计划/放行/指令消费)先落库:增量事件才有独立的可见时刻,
    # 也避免同一 session 里未提交的旧事务挡住 flush 提交。
    await session.commit()
    projector = delta.DeltaProjector(sink)
    try:
        yield projector
    finally:
        await sink.close()


async def _append_assistant_message(
    session: AsyncSession, run: Run, content: str
) -> None:
    """run 到达停点时把"回复"落进对话(控制台直建、无会话的 run 为空操作)。

    这是 agent 在对话里的持久化投影:交付物/失败原因/拒绝原因——
    对话历史因此双向完整,后续 run 的多轮上下文也由此而来。
    """
    if not run.conversation_id:
        return
    await MessageRepository(session).add(
        ChatMessage(
            tenant_id=run.tenant_id,
            conversation_id=run.conversation_id,
            run_id=run.id,
            role="assistant",
            content=content,
        )
    )


async def _fail_run(
    session: AsyncSession, run: Run, exc: Exception, stage: str
) -> None:
    """图执行异常 → failed 终态(§6.2):状态落库 + run.failed.v1,不向调用方裸奔。

    这是图调用边界的统一兜底——节点/LLM/图本身的异常都在这里收敛;
    failed 是终态,重试语义是新起 run。
    """
    run.status = RunStatus.FAILED.value
    run.status_reason = f"{type(exc).__name__}: {exc}"[:500]
    metrics.observe_run_outcome(status=RunStatus.FAILED.value)
    await append_event(
        session,
        run,
        "run.failed.v1",
        {"stage": stage, "reason": run.status_reason},
    )
    await _append_assistant_message(session, run, f"执行失败:{run.status_reason}")


async def list_events(
    session: AsyncSession, tenant_id: str, run_id: str, after: int = 0
) -> list[RunEvent]:
    """事件回放:按 seq 升序(§6.3,SSE 断线重放与审计共用);after 为续传游标。"""
    return await RunEventRepository(session).list_for_run(
        tenant_id, run_id, after=after
    )
