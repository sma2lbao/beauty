"""sandboxd 持久 shell:WebSocket PTY 读写与关闭。

sandboxd 的 PTY 是 WebSocket 协议(见其客户端 SDK 的 `pty_url` /
`auth_headers`):客户端连上后,文本帧是 stdin,服务端下发的文本/二进制帧是
终端输出;控制帧是 JSON:客户端可发 `resize`,服务端可能用 `exit` /
`closed` 告知会话结束。

本模块只负责协议适配,不关心业务层如何聚合输出。PTY 没有可重连的
session id/pid,所以 `attach_shell` 仍由 provider 明确拒绝。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import structlog
from common.errors import UpstreamError

logger = structlog.get_logger(__name__)

# 服务端可能用这些控制帧告知 PTY 已结束;其余 JSON 一律当作终端输出,
# 避免把用户在终端里打印的 JSON 误吞掉。
_EXIT_FRAME_TYPES = frozenset({"exit", "exit_code", "closed"})


def _control_exit(message: str) -> tuple[int | None, bool] | None:
    """识别 PTY 退出控制帧;不是控制帧时返回 None。"""
    stripped = message.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("type") or "")
    if kind not in _EXIT_FRAME_TYPES:
        return None
    raw = payload.get("exit_code")
    if raw is None:
        raw = payload.get("code")
    try:
        code = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        code = None
    return code, True


class SandboxdShellSession:
    """sandboxd WebSocket PTY 上的持久交互式 shell(真 TTY)。

    输出由后台读循环写入 `asyncio.Queue`;连接关闭/进程退出时读循环塞
    `None` 作为 EOF 标记。`exit_code` 只有服务端下发退出控制帧时才有值,
    否则保持 None(上层会按 -1 记录)。
    """

    tty = True

    def __init__(
        self,
        websocket: Any,
        *,
        name: str,
        queue: asyncio.Queue[bytes | None] | None = None,
    ) -> None:
        self._ws = websocket
        self._name = name
        self._queue = queue if queue is not None else asyncio.Queue()
        self._closed = False
        self._eof = False
        self._exit_code: int | None = None
        self._reader = asyncio.create_task(self._read_loop())

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    async def _read_loop(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, bytes):
                    if message:
                        await self._queue.put(bytes(message))
                    continue
                text = message if isinstance(message, str) else str(message)
                control = _control_exit(text)
                if control is not None:
                    code, _ = control
                    if code is not None:
                        self._exit_code = code
                    break
                if text:
                    await self._queue.put(text.encode("utf-8"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 读循环不能把异常留给无人 await 的 task
            logger.debug(
                "sandboxd.pty_read_failed",
                sandbox_name=self._name,
                error=str(exc)[:300],
            )
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
        # sandboxd 的 stdin 是文本帧;终端输入按 UTF-8 发送。
        text = data.decode("utf-8", "replace")
        try:
            await self._ws.send(text)
        except Exception as exc:  # noqa: BLE001 - 连接可能在任何时刻断开
            self._eof = True
            with suppress(Exception):
                self._queue.put_nowait(None)
            raise UpstreamError(
                "sandbox.session_closed",
                "shell 会话已结束,无法再写入",
                details={"sandbox_name": self._name, "error": str(exc)[:300]},
            ) from exc

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._eof = True
        with suppress(Exception):
            await self._ws.close()
        self._reader.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await self._reader
        with suppress(Exception):
            self._queue.put_nowait(None)


__all__ = ["SandboxdShellSession"]
