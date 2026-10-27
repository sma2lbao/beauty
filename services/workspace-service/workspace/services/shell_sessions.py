"""持久 shell 会话的**进程内**管理器 + SSE 帧格式。

设计约束(README「单实例限制」一并有说明):

- 会话状态(输出 ring、订阅者、seq)只在本进程内存里。多副本部署时,同一个
  session_id 的后续请求(写输入/看流/关闭)必须落在同一实例,前端断线重连也要
  命中同一实例;跨实例会得到 404。要真正多实例需要把输出流外置(Redis Stream /
  NATS),不在本阶段范围。
- 每个会话只保留最近 `ring_bytes`(默认 64KB)输出,重连回放受此上限约束:
  更早的输出从旧到新丢弃,但 seq 不回退(客户端可用 Last-Event-ID 续传)。
- 空闲 `idle_seconds`(默认 30 分钟,按"有输入或有输出"算活动)由 sweeper
  关闭;关闭后的会话在内存里再保留 `closed_retention_seconds`,让晚到的连接
  仍能回放 ring buffer 与退出码,然后释放。
- 一个会话可以有多个 SSE 订阅者(fan-out),断开只移除订阅,不关会话。
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import json
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from common.errors import AppError, ConflictError, NotFoundError, UpstreamError

from workspace.core.config import Settings
from workspace.services.sandbox_provider import SandboxProvider, ShellSession

logger = structlog.get_logger(__name__)

ShellEventKind = Literal["output", "exit", "error"]
# 单个会话 ring 里的事件数上限(防止极短输出把内存撑爆)
MAX_RING_EVENTS = 4096
# 读循环收尾时等 shell 退出的上限
CLOSE_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class ShellEvent:
    """会话里的一条输出/退出/错误事件;seq 单调递增,供 SSE 断线续传。"""

    seq: int
    kind: ShellEventKind
    data: str | None = None
    exit_code: int | None = None
    code: str | None = None
    message: str | None = None

    def to_sse(self) -> str:
        """渲染成一条 SSE 帧(JSON data 单行,换行已被转义)。"""
        payload: dict[str, Any] = {"seq": self.seq}
        if self.kind == "output":
            payload["data"] = self.data or ""
        elif self.kind == "exit":
            payload["exit_code"] = self.exit_code if self.exit_code is not None else -1
        else:
            payload["code"] = self.code or "sandbox.session_error"
            payload["message"] = self.message or ""
        encoded = json.dumps(payload, ensure_ascii=False)
        return f"id: {self.seq}\nevent: {self.kind}\ndata: {encoded}\n\n"


@dataclass(frozen=True)
class ShellSessionInfo:
    """开会话的返回:schema 只暴露 session_id/sandbox_id/cwd/created_at。"""

    session_id: str
    sandbox_id: str
    cwd: str
    created_at: datetime
    tty: bool


@dataclass
class _ShellSessionState:
    """一个会话的内部状态(不对外暴露)。"""

    tenant_id: str
    sandbox_id: str
    session_id: str
    runtime_name: str
    cwd: str
    cols: int
    rows: int
    created_at: datetime
    shell: ShellSession
    tty: bool
    decoder: codecs.IncrementalDecoder
    next_seq: int = 0
    buffer: deque[ShellEvent] = field(default_factory=deque)
    buffer_bytes: int = 0
    subscribers: set[asyncio.Queue[ShellEvent | None]] = field(default_factory=set)
    reader: asyncio.Task[None] | None = None
    last_activity: float = 0.0
    closed: bool = False
    closed_at: float | None = None
    exit_code: int | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.sandbox_id, self.session_id)


def _new_decoder() -> codecs.IncrementalDecoder:
    """增量 UTF-8 解码器:跨 read() 边界的多字节字符不会被拆成 U+FFFD。"""
    return codecs.getincrementaldecoder("utf-8")("replace")


class ShellSessionManager:
    """进程内会话表(键 = tenant_id / sandbox_id / session_id)。

    `clock` 可注入,便于测试空闲回收而不真的等 30 分钟。
    """

    def __init__(
        self,
        *,
        idle_seconds: float = 1800.0,
        closed_retention_seconds: float = 300.0,
        ring_bytes: int = 65_536,
        max_per_sandbox: int = 8,
        max_subscribers: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_seconds = idle_seconds
        self._closed_retention_seconds = closed_retention_seconds
        self._ring_bytes = ring_bytes
        self._max_per_sandbox = max_per_sandbox
        self._max_subscribers = max_subscribers
        self._clock = clock
        self._sessions: dict[tuple[str, str, str], _ShellSessionState] = {}
        self._shutdown = False

    @property
    def count(self) -> int:
        """当前内存里的会话数(含已关闭、等待回放保留期的)。"""
        return len(self._sessions)

    async def open(
        self,
        *,
        tenant_id: str,
        sandbox_id: str,
        runtime_name: str,
        provider: SandboxProvider,
        cwd: str,
        env: dict[str, str] | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> ShellSessionInfo:
        if self._shutdown:
            raise ConflictError("sandbox.session_closed", "服务正在关闭,暂不接受新会话")
        active = sum(
            1
            for state in self._sessions.values()
            if state.tenant_id == tenant_id
            and state.sandbox_id == sandbox_id
            and not state.closed
        )
        if active >= self._max_per_sandbox:
            raise ConflictError(
                "sandbox.session_limit",
                f"沙箱内活跃 shell 会话已达上限({self._max_per_sandbox})",
            )
        shell = await provider.open_shell(
            runtime_name, cwd=cwd, env=env, cols=cols, rows=rows
        )
        session_id = uuid.uuid4().hex
        now = self._clock()
        state = _ShellSessionState(
            tenant_id=tenant_id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            runtime_name=runtime_name,
            cwd=cwd,
            cols=cols,
            rows=rows,
            created_at=datetime.now(UTC),
            shell=shell,
            tty=bool(getattr(shell, "tty", False)),
            decoder=_new_decoder(),
            last_activity=now,
        )
        self._sessions[state.key] = state
        state.reader = asyncio.create_task(
            self._pump(state), name=f"shell-session-{session_id}"
        )
        logger.info(
            "shell_session.opened",
            tenant_id=tenant_id,
            sandbox_id=sandbox_id,
            session_id=session_id,
            tty=state.tty,
            cols=cols,
            rows=rows,
        )
        return ShellSessionInfo(
            session_id=session_id,
            sandbox_id=sandbox_id,
            cwd=cwd,
            created_at=state.created_at,
            tty=state.tty,
        )

    async def write(
        self, *, tenant_id: str, sandbox_id: str, session_id: str, data: bytes
    ) -> None:
        state = self._require(tenant_id, sandbox_id, session_id)
        if state.closed:
            raise ConflictError("sandbox.session_closed", "shell 会话已结束")
        try:
            await state.shell.write(data)
        except UpstreamError as exc:
            if exc.code == "sandbox.session_closed":
                raise ConflictError(
                    "sandbox.session_closed", "shell 会话已结束"
                ) from exc
            raise
        state.last_activity = self._clock()

    async def close(self, *, tenant_id: str, sandbox_id: str, session_id: str) -> bool:
        """关闭会话;返回本次是否真的从"运行中"被关掉(幂等)。"""
        state = self._sessions.get((tenant_id, sandbox_id, session_id))
        if state is None:
            raise NotFoundError(
                "sandbox.session_not_found",
                "shell 会话不存在",
                details={"session_id": session_id},
            )
        if state.closed:
            return False
        await self._close_state(state)
        return True

    async def close_sandbox(self, tenant_id: str, sandbox_id: str) -> int:
        """关闭某个沙箱的全部会话(沙箱被销毁/过期时调用),返回关闭个数。"""
        states = [
            state
            for state in list(self._sessions.values())
            if state.tenant_id == tenant_id and state.sandbox_id == sandbox_id
        ]
        closed = 0
        for state in states:
            if state.closed:
                continue
            await self._close_state(state)
            closed += 1
        return closed

    def subscribe(
        self,
        *,
        tenant_id: str,
        sandbox_id: str,
        session_id: str,
        after: int | None = None,
    ) -> tuple[list[ShellEvent], asyncio.Queue[ShellEvent | None]]:
        """订阅会话:返回(回放缓冲, 实时队列)。

        回放 + 注册队列在同一同步段完成,不会与读循环交错,因此不丢事件。
        会话已关闭时队列里直接放一个 None(回放完就结束)。
        """
        state = self._require(tenant_id, sandbox_id, session_id)
        backlog = [
            event for event in state.buffer if after is None or event.seq > after
        ]
        queue: asyncio.Queue[ShellEvent | None] = asyncio.Queue()
        if state.closed:
            queue.put_nowait(None)
        else:
            if len(state.subscribers) >= self._max_subscribers:
                raise ConflictError(
                    "sandbox.session_busy", "该会话的订阅者过多,请稍后重试"
                )
            state.subscribers.add(queue)
        state.last_activity = self._clock()
        return backlog, queue

    def unsubscribe(
        self,
        *,
        tenant_id: str,
        sandbox_id: str,
        session_id: str,
        queue: asyncio.Queue[ShellEvent | None],
    ) -> None:
        """断开订阅(不关会话)。"""
        state = self._sessions.get((tenant_id, sandbox_id, session_id))
        if state is not None:
            state.subscribers.discard(queue)

    async def sweep(self, *, now: float | None = None) -> int:
        """回收空闲会话 + 清理过了回放保留期的已关闭会话;返回关掉的会话数。"""
        moment = self._clock() if now is None else now
        closed_count = 0
        for state in list(self._sessions.values()):
            if state.closed:
                if (
                    state.closed_at is not None
                    and not state.subscribers
                    and moment - state.closed_at >= self._closed_retention_seconds
                ):
                    self._sessions.pop(state.key, None)
                continue
            if moment - state.last_activity >= self._idle_seconds:
                logger.info(
                    "shell_session.idle_reaped",
                    tenant_id=state.tenant_id,
                    sandbox_id=state.sandbox_id,
                    session_id=state.session_id,
                    idle_seconds=self._idle_seconds,
                )
                await self._close_state(state)
                closed_count += 1
        return closed_count

    async def shutdown(self) -> None:
        """进程关停兜底:关掉所有会话,之后不再接受新会话。"""
        self._shutdown = True
        for state in list(self._sessions.values()):
            await self._close_state(state)
        self._sessions.clear()

    # --- 内部 ---

    def _require(
        self, tenant_id: str, sandbox_id: str, session_id: str
    ) -> _ShellSessionState:
        state = self._sessions.get((tenant_id, sandbox_id, session_id))
        if state is None:
            raise NotFoundError(
                "sandbox.session_not_found",
                "shell 会话不存在",
                details={"session_id": session_id},
            )
        return state

    def _record(
        self,
        state: _ShellSessionState,
        *,
        kind: ShellEventKind,
        data: str | None = None,
        exit_code: int | None = None,
        code: str | None = None,
        message: str | None = None,
    ) -> ShellEvent:
        event = ShellEvent(
            seq=state.next_seq,
            kind=kind,
            data=data,
            exit_code=exit_code,
            code=code,
            message=message,
        )
        state.next_seq += 1
        state.buffer.append(event)
        state.buffer_bytes += len(data.encode("utf-8")) if data else 0
        while state.buffer and (
            state.buffer_bytes > self._ring_bytes or len(state.buffer) > MAX_RING_EVENTS
        ):
            dropped = state.buffer.popleft()
            state.buffer_bytes -= (
                len(dropped.data.encode("utf-8")) if dropped.data else 0
            )
        for queue in list(state.subscribers):
            queue.put_nowait(event)
        state.last_activity = self._clock()
        return event

    def _finalize(self, state: _ShellSessionState, exit_code: int | None) -> None:
        """读循环收尾:记一条 exit 事件并唤醒所有订阅者。"""
        if state.closed:
            return
        state.closed = True
        state.closed_at = self._clock()
        code = int(exit_code) if exit_code is not None else -1
        state.exit_code = code
        self._record(state, kind="exit", exit_code=code)
        for queue in list(state.subscribers):
            queue.put_nowait(None)
        state.subscribers.clear()
        logger.info(
            "shell_session.exited",
            tenant_id=state.tenant_id,
            sandbox_id=state.sandbox_id,
            session_id=state.session_id,
            exit_code=code,
        )

    async def _pump(self, state: _ShellSessionState) -> None:
        """后台读循环:把 shell 输出投递给 ring buffer 与所有订阅者。"""
        try:
            while True:
                data = await state.shell.read()
                if data is None:
                    break
                if not data:
                    continue
                text = state.decoder.decode(bytes(data))
                if text:
                    self._record(state, kind="output", data=text)
        except asyncio.CancelledError:
            raise
        except AppError as exc:
            self._record(state, kind="error", code=exc.code, message=exc.message)
        except Exception as exc:  # noqa: BLE001 - 读循环不能把异常留给无人 await 的 task
            logger.warning(
                "shell_session.read_failed",
                session_id=state.session_id,
                error=str(exc)[:300],
            )
            self._record(
                state,
                kind="error",
                code="sandbox.session_error",
                message=str(exc)[:300],
            )
        finally:
            tail = state.decoder.decode(b"", True)
            if tail:
                self._record(state, kind="output", data=tail)
            self._finalize(state, state.shell.exit_code)

    async def _close_state(self, state: _ShellSessionState) -> None:
        """关会话:先关 shell,再等读循环收完退出码;读循环卡住则取消。"""
        if state.closed:
            return
        try:
            await state.shell.close()
        except Exception as exc:  # noqa: BLE001 - 关会话尽力而为,不能阻断清理
            logger.warning(
                "shell_session.close_failed",
                session_id=state.session_id,
                error=str(exc)[:300],
            )
        task = state.reader
        if task is not None and not task.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(task), timeout=CLOSE_GRACE_SECONDS
                )
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if not state.closed:
            self._finalize(state, state.shell.exit_code)


async def iter_sse_frames(
    backlog: list[ShellEvent],
    queue: asyncio.Queue[ShellEvent | None],
    *,
    heartbeat_seconds: float,
    on_close: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    """先回放 backlog,再跟随实时事件;空闲超过心跳间隔发注释行保活。"""
    try:
        for event in backlog:
            yield event.to_sse()
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                return
            yield event.to_sse()
    finally:
        if on_close is not None:
            on_close()


def build_shell_session_manager(settings: Settings) -> ShellSessionManager:
    """按配置装配会话管理器(单实例:每个进程一个)。"""
    return ShellSessionManager(
        idle_seconds=float(settings.sandbox_shell_idle_seconds),
        closed_retention_seconds=float(settings.sandbox_shell_closed_retention_seconds),
        ring_bytes=settings.sandbox_shell_ring_bytes,
        max_per_sandbox=settings.sandbox_shell_max_per_sandbox,
        max_subscribers=settings.sandbox_shell_max_subscribers,
    )
