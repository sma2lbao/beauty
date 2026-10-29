"""本地 provider 的 shell 会话:POSIX pty 真 TTY + 管道退化,及进程/pty 工具。

`pty` / `fcntl` / `termios` 的可用性在本模块判定;测试要覆盖退化路径时
monkeypatch 的是 `workspace.services.sandbox_provider.local` 模块上的同名全局
(provider 侧在 `_open_pty_shell` 里按名字解析)。
"""

from __future__ import annotations

import asyncio
import os
import signal
import struct
import sys
import time
from contextlib import suppress
from typing import Any

from common.errors import UpstreamError

from .protocols import SHELL_READ_CHUNK

try:  # POSIX pty 支持;Windows 上导入失败 → local provider 退化管道 shell
    import fcntl
    import pty
    import termios
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    pty = None  # type: ignore[assignment]
    termios = None  # type: ignore[assignment]


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    """结束子进程:POSIX 按进程组杀,避免 shell 的孙进程残留。"""
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    with suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5)


async def _process_exit_code(
    process: asyncio.subprocess.Process, *, timeout: float = 5
) -> int:
    """等子进程退出并取退出码;超时先杀进程组,再取不到就用 -1。"""
    try:
        code = await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        await _terminate_process(process)
        with suppress(Exception):
            await asyncio.wait_for(process.wait(), timeout=timeout)
        code = process.returncode
    return int(code) if code is not None else -1


def _set_winsize(fd: int, cols: int, rows: int) -> None:
    """设置 pty 窗口大小(TIOCSWINSZ);失败不影响读写。"""
    if fcntl is None or termios is None:
        return
    with suppress(OSError):
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _pty_child_setup(slave_fd: int) -> Any:
    """子进程 fork 后:新建会话并把这个 pty 设为控制终端(拿到真 TTY 语义)。"""
    if fcntl is None or termios is None:
        return None

    def _setup() -> None:
        os.setsid()
        with suppress(OSError):
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

    return _setup


def _pipe_shell_argv() -> list[str]:
    """没有 pty 时的退化 shell 命令(POSIX 用 sh -i,Windows 用 COMSPEC)。"""
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return ["/bin/sh", "-i"]


class LocalPipeShellSession:
    """管道版 shell:stdin/stdout 是普通管道,**不是 TTY**。

    pty 不可用时(Windows、没有 /dev/ptmx 的受限容器)的退化实现:没有行编辑、
    补全、回显与作业控制,`isatty()` 为 False(很多程序因此切到非交互模式)。
    stderr 已合流到 stdout,所以 read() 只有一条流。
    """

    tty = False

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._exit_code: int | None = None
        self._closed = False
        self._eof = False

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    async def read(self) -> bytes | None:
        if self._eof:
            return None
        stdout = self._process.stdout
        if stdout is None:
            self._eof = True
            return None
        data = await stdout.read(SHELL_READ_CHUNK)
        if not data:
            self._eof = True
            if self._exit_code is None:
                self._exit_code = await _process_exit_code(self._process)
            return None
        return bytes(data)

    async def write(self, data: bytes) -> None:
        stdin = self._process.stdin
        if self._closed or stdin is None:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        try:
            stdin.write(data)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise UpstreamError(
                "sandbox.session_closed", "shell 会话已结束,无法再写入"
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        if self._process.stdin is not None:
            with suppress(Exception):
                self._process.stdin.close()
        await _terminate_process(self._process)
        if self._exit_code is None:
            self._exit_code = await _process_exit_code(self._process)


class LocalPtyShellSession:
    """POSIX pty 上的真 TTY shell:有行编辑/补全/回显,`isatty()` 为 True。

    master fd 交给事件循环的 add_reader,读到 EOF(对端全关,Linux 上 EIO)
    时投递 None 作为流结束;写输入按 pty 缓冲分块并等待可写,避免阻塞事件循环。
    """

    tty = True

    # 单次 os.write 的字节数:pty 缓冲通常 4KB,分块写避免一次塞满阻塞
    _WRITE_CHUNK = 1024
    _WRITE_TIMEOUT_SECONDS = 5

    def __init__(self, process: asyncio.subprocess.Process, master_fd: int) -> None:
        self._process = process
        self._master_fd = master_fd
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._exit_code: int | None = None
        self._closed = False
        self._eof = False
        self._loop.add_reader(master_fd, self._on_readable)

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def _on_readable(self) -> None:
        try:
            chunk = os.read(self._master_fd, SHELL_READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            chunk = b""
        if chunk:
            self._queue.put_nowait(bytes(chunk))
            return
        self._remove_reader()
        self._queue.put_nowait(None)

    def _remove_reader(self) -> None:
        with suppress(Exception):
            self._loop.remove_reader(self._master_fd)

    async def read(self) -> bytes | None:
        if self._eof:
            return None
        chunk = await self._queue.get()
        if chunk is None:
            self._eof = True
            if self._exit_code is None:
                self._exit_code = await _process_exit_code(self._process)
            return None
        return chunk

    async def write(self, data: bytes) -> None:
        if self._closed or self._eof:
            raise UpstreamError("sandbox.session_closed", "shell 会话已结束,无法再写入")
        remaining = memoryview(data)
        deadline = time.monotonic() + self._WRITE_TIMEOUT_SECONDS
        while remaining:
            try:
                written = os.write(self._master_fd, remaining[: self._WRITE_CHUNK])
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise UpstreamError(
                        "sandbox.session_closed", "shell 输入写入超时"
                    ) from None
                await asyncio.sleep(0.005)
                continue
            except OSError as exc:
                raise UpstreamError(
                    "sandbox.session_closed", "shell 会话已结束,无法再写入"
                ) from exc
            remaining = remaining[written:]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        self._remove_reader()
        with suppress(OSError):
            os.close(self._master_fd)
        await _terminate_process(self._process)
        if self._exit_code is None:
            self._exit_code = await _process_exit_code(self._process)
        self._queue.put_nowait(None)
