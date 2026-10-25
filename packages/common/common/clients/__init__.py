"""对外部服务的 HTTP 客户端(进程内可注入 transport,测试零网络)。"""

from __future__ import annotations

from common.clients.sandbox import (
    ERROR_BODY_LIMIT,
    UNREACHABLE_CODE,
    UPSTREAM_ERROR_CODE,
    Sandbox,
    SandboxClient,
    SandboxExecResult,
    SandboxInfo,
)

__all__ = [
    "ERROR_BODY_LIMIT",
    "UNREACHABLE_CODE",
    "UPSTREAM_ERROR_CODE",
    "Sandbox",
    "SandboxClient",
    "SandboxExecResult",
    "SandboxInfo",
]
