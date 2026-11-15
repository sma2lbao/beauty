"""持久 shell 的 SSE 事件模型与帧渲染(纯数据,不碰会话状态)。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

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
