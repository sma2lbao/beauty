"""持久 shell 会话:进程内会话表 + 跨实例所有权登记与接管(门面)。

对外只暴露门面名:会话管理器、登记表、事件模型与 SSE 渲染。实现分在
`events`(纯数据)、`state`(会话状态)、`registry`(登记表)、`manager`(调度)。
"""

from __future__ import annotations

from workspace.services.shell_sessions.events import (
    CLOSE_GRACE_SECONDS,
    MAX_RING_EVENTS,
    ShellEvent,
    ShellEventKind,
    ShellSessionInfo,
    iter_sse_frames,
)
from workspace.services.shell_sessions.manager import (
    ShellSessionManager,
    build_shell_session_manager,
)
from workspace.services.shell_sessions.registry import (
    ShellSessionClaim,
    ShellSessionRegistry,
    build_shell_session_registry,
    resolve_instance_id,
)

__all__ = [
    "CLOSE_GRACE_SECONDS",
    "MAX_RING_EVENTS",
    "ShellEvent",
    "ShellEventKind",
    "ShellSessionClaim",
    "ShellSessionInfo",
    "ShellSessionManager",
    "ShellSessionRegistry",
    "build_shell_session_manager",
    "build_shell_session_registry",
    "iter_sse_frames",
    "resolve_instance_id",
]
