"""E2B 持久 shell:PTY 读写与关闭。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from common.errors import UpstreamError

from workspace.services.sandbox_provider.utils import _error_details


class E2bShellSession:
    """`sandbox.pty` 上的持久交互式 shell(真 TTY)。

    输出由 pty 的 on_data 回调写入 `asyncio.Queue`;会话结束时 watcher 塞
    `None` 作为 EOF 标记,读端据此返回 None。
    """

    tty = True

    def __init__(
        self,
        sandbox: Any,
        handle: Any,
        *,
        name: str,
        queue: asyncio.Queue[bytes | None],
    ) -> None:
        self._sandbox = sandbox
        self._handle = handle
        self._name = name
        self._queue = queue
        self._closed = False
        self._eof = False
        self._exit_code: int | None = None
        self._watcher = asyncio.create_task(self._watch())

    @property
    def pid(self) -> int:
        """pty 进程号(写进会话登记表,用于换实例后 `pty.connect` 接管)。"""
        return int(self._handle.pid)

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    async def _watch(self) -> None:
        """等 pty 进程退出;正常/异常退出都只记录退出码,不向上抛。"""
        try:
            result = await self._handle.wait()
            code = getattr(result, "exit_code", None)
            if code is not None:
                self._exit_code = int(code)
        except BaseException as exc:  # noqa: BLE001 - 退出码是可选信息
            code = getattr(exc, "exit_code", None)
            if code is not None:
                self._exit_code = int(code)
        finally:
            self._eof = True
            with suppress(Exception):
                self._queue.put_nowait(None)

    async def read(self) -> bytes | None:
        if self._eof and self._queue.empty():
            return None
        chunk = await self._queue.get()
        if chunk is None:
            self._eof = True
            return None
        return chunk

    async def write(self, data: bytes) -> None:
        if self._closed or self._eof:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        try:
            await self._sandbox.pty.send_stdin(self._handle.pid, data)
        except Exception as exc:
            raise UpstreamError(
                "sandbox.session_closed",
                "shell 会话已结束,无法再写入",
                details=_error_details(self._name, exc),
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        with suppress(Exception):
            await self._sandbox.pty.kill(self._handle.pid)
        self._watcher.cancel()
        with suppress(BaseException):
            await self._watcher
        with suppress(Exception):
            self._queue.put_nowait(None)
