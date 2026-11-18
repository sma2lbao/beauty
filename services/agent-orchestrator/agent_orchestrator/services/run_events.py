"""run 事件:写入(seq 单调)、正文增量攒批、回放查询与失败终态投影。

事件信封负责命名/版本校验(§6.3);正文增量只走 DB 回放,不进 outbox。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from events.envelope import EventEnvelope
from sqlalchemy import select
from sqlalchemy.exc import (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    PendingRollbackError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.graph import delta, items, steps
from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.models.runs import Run, RunEvent
from agent_orchestrator.repositories import MessageRepository, RunEventRepository
from agent_orchestrator.schemas.runs import RunStatus

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from agent_orchestrator.core.config import Settings


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
    env = EventEnvelope.create(type_, payload, account_id=run.account_id)
    events = RunEventRepository(session)
    event = RunEvent(
        id=env.id,
        account_id=run.account_id,
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


async def is_cancelled(session: AsyncSession, run_id: str) -> bool:
    """协作式取消(P0-2):执行边界回来时读最新**已提交**状态,已取消则收手。

    只读一条 SELECT 而不 refresh ORM 对象——本事务里可能已有未提交的投影写入,
    refresh 会把它们丢掉;取消请求来自另一会话,READ COMMITTED 下这条查询能看到。
    """
    status = await session.scalar(select(Run.status).where(Run.id == run_id))
    return status == RunStatus.CANCELLED.value


#: 执行边界读到的"已被外部收尾"状态(P1-C):取消与超时失败都要丢弃迟到结果。
_SUPERSEDING_STATUSES = frozenset({RunStatus.CANCELLED.value, RunStatus.FAILED.value})


async def is_superseded(session: AsyncSession, run_id: str) -> str | None:
    """执行边界回来时读库:被取消或已被超时 sweeper 判 failed 则返回该状态。

    比 is_cancelled 多覆盖 failed——超时是"另一条会话条件更新"的终态,写库后
    图仍在跑;迟到的 completed 结果若覆盖它,就丢掉了超时原因和可重试语义。
    只读一条 SELECT 的约束同 is_cancelled(不 refresh ORM 对象)。
    """
    status = await session.scalar(select(Run.status).where(Run.id == run_id))
    return status if status in _SUPERSEDING_STATUSES else None


class _RunDeltaSink:
    """把节点上报的正文增量攒成 ``token.delta.v1`` 事件(§6.3)。

    与图调用共用同一个 session 并逐批提交:每批因此有各自可见的 seq 时刻,
    SSE 回放能按序拼回整段正文,前端也能边收边渲染。攒批口径是"字数或时长
    先到者"(默认 240 字 / 0.25 秒)——逐字写库会把一次 run 变成成百上千行
    事件,轮询回放也扛不住。
    """

    def __init__(self, session: AsyncSession, run: Run, settings: Settings) -> None:
        self._session = session
        self._run = run
        # 攒批口径与开关都从本次调用的启动配置来(不再读进程级全局):
        # 同一进程里不同 app 实例(测试/多租户装配)各有各的 settings。
        self._flush_chars = max(1, settings.run_delta_flush_chars)
        self._flush_seconds = max(0.0, settings.run_delta_flush_seconds)
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
    session: AsyncSession, run: Run, settings: Settings
) -> AsyncIterator[delta.DeltaProjector]:
    """图调用边界:构造本次 run 的正文增量投影器(未开启时为空投影器)。

    投影器经 graph invocation config 显式注入节点(不再用 ContextVar 绑定),
    图内节点只上报、落库与提交仍在这里完成。开关与攒批口径取自本次调用的
    settings(进程级 configure 兼容层已删,R-29)。
    """
    if not settings.run_delta_streaming:
        yield delta.DeltaProjector()
        return
    sink = _RunDeltaSink(session, run, settings)
    # 开跑前的写入(计划/放行/指令消费)先落库:增量事件才有独立的可见时刻,
    # 也避免同一 session 里未提交的旧事务挡住 flush 提交。
    await session.commit()
    projector = delta.DeltaProjector(sink)
    try:
        yield projector
    finally:
        await sink.close()


class _RunStepSink:
    """步骤 span 收集器:节点一结束就落成 run.step.v1(S4),调用边界兜底。"""

    def __init__(self, session: AsyncSession, run: Run) -> None:
        self._session = session
        self._run = run
        self.spans: list[steps.StepSpan] = []

    def push(self, span: steps.StepSpan) -> None:
        self.spans.append(span)

    async def flush(self) -> None:
        """把已结束的 span 立刻落库并提交:SSE/轮询不必等整段图跑完。

        步骤是观测面:落库故障记 warning 但不让节点因此失败(节点里的副作用
        已经发生了,把它标成 failed 只会误导)。退出时 _step_stream 还会再兜底
        落一次(那时 spans 已被清空,通常是 no-op)。
        """
        if not self.spans:
            return
        try:
            await _flush_steps(self._session, self._run, self)
            await self._session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("step flush failed", run_id=self._run.id, exc_info=True)
            # 连接被判定失效后,同一会话后续的每条语句都会抛 PendingRollback。
            # 这里回滚让会话重新可用,否则"观测面落库失败"会顺带把整个 job 打死。
            await _reset_if_invalid(self._session, exc)


#: 连接级错误:出现这些之后同一会话的后续语句都会连环失败,必须回滚复位。
_CONNECTION_ERRORS = (
    PendingRollbackError,
    OperationalError,
    InterfaceError,
    DisconnectionError,
)


async def _reset_if_invalid(session: AsyncSession, exc: BaseException) -> None:
    """连接失效(锁冲突/断连)后回滚会话,避免后续语句连环 PendingRollback。

    只对连接级错误回滚:业务错误(比如约束冲突)保持在调用方手里,不在这里
    悄悄丢掉待提交状态。
    """
    if not isinstance(exc, _CONNECTION_ERRORS):
        return
    try:
        await session.rollback()
    except Exception:  # noqa: BLE001 - 回滚失败也没别的补救手段
        logger.warning("session rollback after flush failure failed", exc_info=True)


async def _flush_steps(session: AsyncSession, run: Run, sink: _RunStepSink) -> None:
    """把本次图调用的 span 落成 run.step.v1(展示/归因记录,不进 outbox)。

    step_id/round 由 StepProjector.begin 分配(构造时预读同 (attempt, node) 的
    历史计数):恢复/返工让同一节点再跑时序号自然推进,step_id 稳定可寻址
    (07 S1 契约);这里只做 payload 固化。
    """
    if not sink.spans:
        return
    for span in sink.spans:
        round_no = span.round or 1
        await append_event(
            session,
            run,
            steps.EVENT_TYPE,
            {
                "step_id": span.step_id
                or f"{run.id}:{run.attempts}:{span.node}:{round_no}",
                "node": span.node,
                "phase": span.phase,
                "attempt": run.attempts,
                "round": round_no,
                "status": span.status,
                "started_at": span.started_at.isoformat(),
                "finished_at": (
                    span.finished_at.isoformat() if span.finished_at else None
                ),
                "duration_ms": span.duration_ms,
            },
            record_outbox=False,
        )
    sink.spans.clear()


async def _step_rounds(session: AsyncSession, run: Run) -> dict[str, int]:
    """本次 attempt 内各节点已落库的步骤数:StepProjector 据此续编号。"""

    rounds: dict[str, int] = {}
    for event in await RunEventRepository(session).list_for_run(run.account_id, run.id):
        if event.type != steps.EVENT_TYPE:
            continue
        payload = event.payload or {}
        if int(payload.get("attempt") or 0) != run.attempts:
            continue
        node = str(payload.get("node") or "")
        rounds[node] = rounds.get(node, 0) + 1
    return rounds


@asynccontextmanager
async def _step_stream(
    session: AsyncSession, run: Run
) -> AsyncIterator[steps.StepProjector]:
    """采集节点步骤:节点结束即落库(S4),退出时兜底(异常也落,失败 span 带失败状态)。

    退出顺序保证"步骤 → 终态":本上下文先于调用方的 except 兜底执行,
    所以 run.failed.v1 一定排在失败步骤之后。
    """
    sink = _RunStepSink(session, run)
    projector = steps.StepProjector(
        sink,
        run_id=run.id,
        attempt=run.attempts,
        rounds=await _step_rounds(session, run),
    )
    try:
        yield projector
    finally:
        await _flush_steps(session, run, sink)


@dataclass(slots=True)
class _ItemSnapshot:
    """一次 push 的相位快照(span 本体随后还会被改写,落库要用当时的值)。"""

    span: items.ItemSpan
    phase: str
    status: str | None
    started_at: datetime
    finished_at: datetime | None
    data: dict[str, Any]


class _RunItemSink:
    """图调用期间的过程项 span 收集器:调用边界统一落成 run.item.v1。"""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.records: list[_ItemSnapshot] = []

    def push(self, span: items.ItemSpan) -> None:
        self.records.append(
            _ItemSnapshot(
                span=span,
                phase=span.phase,
                status=span.status,
                started_at=span.started_at,
                finished_at=span.finished_at,
                data=dict(span.data),
            )
        )


async def _flush_items(session: AsyncSession, run: Run, sink: _RunItemSink) -> None:
    """把过程项相位快照落成 run.item.v1(展示/回放记录,不进 outbox)。

    item 序号在这里按已落库的 (attempt, kind) 项数续编(不是 begin 时):todo
    快照 item 由工具 handler 的独立 session 在 run 进行中先落库,begin 时分配
    会与它撞号;同一 span 的 started/completed 两次 push 复用同一 item_id。
    """
    if not sink.records:
        return
    numbers: dict[str, set[int]] = {}
    for event in await RunEventRepository(session).list_for_run(run.account_id, run.id):
        if event.type != items.EVENT_TYPE:
            continue
        payload = event.payload or {}
        if int(payload.get("attempt") or 0) != run.attempts:
            continue
        item_id = str(payload.get("item_id") or "")
        if ":plan:" in item_id:
            continue
        suffix = item_id.rsplit(":", 1)[-1]
        if suffix.isdigit():
            kind = str(payload.get("kind") or "")
            numbers.setdefault(kind, set()).add(int(suffix))
    counters = {kind: max(values) for kind, values in numbers.items()}
    for record in sink.records:
        span = record.span
        if span.item_id is None:
            number = counters.get(span.kind, 0) + 1
            counters[span.kind] = number
            span.item_id = f"{run.id}:{run.attempts}:{span.kind}:{number}"
        item_payload: dict[str, Any] = {
            "item_id": span.item_id,
            "thread_id": sink.thread_id,
            "turn_id": run.id,
            "kind": span.kind,
            "phase": record.phase,
            "node": span.node,
            "step_id": span.step_id,
            "attempt": run.attempts,
            "title": span.title,
            "data": record.data,
            "started_at": record.started_at.isoformat(),
        }
        if record.status is not None:
            item_payload["status"] = record.status
        if record.finished_at is not None:
            item_payload["finished_at"] = record.finished_at.isoformat()
            item_payload["duration_ms"] = int(
                (record.finished_at - record.started_at).total_seconds() * 1000
            )
        await append_event(
            session, run, items.EVENT_TYPE, item_payload, record_outbox=False
        )
    sink.records.clear()


@asynccontextmanager
async def _item_stream(
    session: AsyncSession,
    run: Run,
    step_projector: steps.StepProjector | None = None,
    *,
    thread_id: str | None = None,
) -> AsyncIterator[items.ItemProjector]:
    """图调用边界:采集过程项(契约 §7);thread_id 在此解析、item 在此落库。

    进入时先提交已排队的写入(计划 started/放行/指令消费):工具 handler 用的是
    独立 session(todo_write 会写 run_events),主 session 挂着未提交的写事务会在
    SQLite 上把它挡成 "database is locked"(与 _delta_stream 开跑前 commit 同一
    先例)。
    """
    # 局部 import:services.items 依赖本模块的 append_event,顶层互引会成环
    from agent_orchestrator.services.items import resolve_thread_id

    resolved = thread_id or await resolve_thread_id(session, run)
    sink = _RunItemSink(resolved)
    await session.commit()
    getter: Callable[[], str | None] | None = None
    if step_projector is not None:
        getter = step_projector.current_step_id
    projector = items.ItemProjector(sink, step_id=getter)
    try:
        yield projector
    finally:
        await _flush_items(session, run, sink)


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
            account_id=run.account_id,
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

    协作式收尾优先(P0-2/P1-C):取消或超时失败已提交时不再写 failed,否则会
    覆盖已有的终态与 status_reason(超时原因要保留给前端/重试提示)。
    """
    if await is_superseded(session, run.id):
        return
    run.status = RunStatus.FAILED.value
    run.status_reason = f"{type(exc).__name__}: {exc}"[:500]
    metrics.observe_run_outcome(status=RunStatus.FAILED.value)
    await append_event(
        session,
        run,
        "run.failed.v1",
        {"stage": stage, "reason": run.status_reason},
    )
    # 规则基线评分(Langfuse 启用时才写):失败记 outcome=0,便于控制台按分数筛选。
    from agent_orchestrator.services import (
        run_scores,  # 局部导入:避免与 append_event 循环
    )

    await run_scores.record_rule_score(session, run, completed=False)
    await _append_assistant_message(session, run, f"执行失败:{run.status_reason}")
    # 子 run 崩了也要叫醒父 run(L2):父 run 拿到失败回执,而不是永久挂起
    from agent_orchestrator.services import subagent_wake

    await subagent_wake.notify_parent(session, run)


async def list_events(
    session: AsyncSession,
    account_id: str,
    run_id: str,
    after: int = 0,
    *,
    include_steps: bool = False,
) -> list[RunEvent]:
    """事件回放:按 seq 升序(§6.3,SSE 断线重放与审计共用);after 为续传游标。

    run.step.v1 是步骤视图(S1)的底层记录,默认不进用户事件回放——步骤时间线
    统一走 GET /runs/{id}/steps,避免事件流被每节点一条的噪声淹没。

    `include_steps=True` 给会话工作台用:步骤要在「事件到达时」就出现,而前端
    读模型的 1.5s 轮询属于重复请求(步骤本来就是事件投影)。S4 之后步骤按节点
    边界落库,订阅方拿到的就是增量的、有序的步骤事件。
    """
    events = await RunEventRepository(session).list_for_run(
        account_id, run_id, after=after
    )
    if include_steps:
        return events
    return [event for event in events if event.type != steps.EVENT_TYPE]
