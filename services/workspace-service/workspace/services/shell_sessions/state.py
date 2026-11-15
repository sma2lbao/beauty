"""一个持久 shell 会话的进程内状态(不对外暴露)。"""

from __future__ import annotations

import asyncio
import codecs
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from workspace.services.sandbox_provider import ShellSession
from workspace.services.shell_sessions.events import ShellEvent


@dataclass
class _ShellSessionState:
    """一个会话的内部状态:shell 句柄 + 输出 ring + 订阅者 + 归属信息。"""

    account_id: str
    sandbox_id: str
    session_id: str
    runtime_name: str
    provider_name: str
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
        return (self.account_id, self.sandbox_id, self.session_id)


def _new_decoder() -> codecs.IncrementalDecoder:
    """增量 UTF-8 解码器:跨 read() 边界的多字节字符不会被拆成 U+FFFD。"""
    return codecs.getincrementaldecoder("utf-8")("replace")
