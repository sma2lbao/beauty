"""读循环与收尾:把 shell 输出投进 ring buffer,并通知所有订阅者。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import structlog
from common.errors import AppError

from workspace.services.shell_sessions.events import (
    CLOSE_GRACE_SECONDS,
    MAX_RING_EVENTS,
    ShellEvent,
    ShellEventKind,
)
from workspace.services.shell_sessions.state import _ShellSessionState

logger = structlog.get_logger(__name__)


class _PumpMixin:
    """会话事件写入与读循环(由 ShellSessionManager 组合)。"""

    # 以下属性由 ShellSessionManager.__init__ 注入
    _ring_bytes: int
    _clock: Callable[[], float]

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
            account_id=state.account_id,
            sandbox_id=state.sandbox_id,
            session_id=state.session_id,
            exit_code=code,
        )
        self._on_session_closed(state)

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
