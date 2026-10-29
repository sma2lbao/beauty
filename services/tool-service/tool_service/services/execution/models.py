"""执行相关的结果模型与耗时口径。

ExecutionContext 是一次调用的调用方上下文;ExecutionOutcome 把结果信封与
可落审计的附加信息(沙箱 id、服务端补丁)绑在一起返回给 API 层。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from common.tools import ToolDescriptor, ToolExecuteResponse


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """一次工具执行的调用方上下文(租户来自 JWT,其余来自请求体)。"""

    tenant_id: str
    tool_call_id: str
    run_id: str | None = None
    conversation_id: str | None = None
    workspace_id: str | None = None
    sandbox_scope: str | None = None
    timeout_seconds: float | None = None
    approved: bool = False


@dataclass(frozen=True, slots=True)
class PatchAudit:
    """写工具的服务端补丁审计(已从工具结果里剥离)。"""

    patch: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    patch_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """执行结果 + 可落审计的附加信息。"""

    descriptor: ToolDescriptor
    response: ToolExecuteResponse
    sandbox_id: str | None = None
    patch: PatchAudit | None = None


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
