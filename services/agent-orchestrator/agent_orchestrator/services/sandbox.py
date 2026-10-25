"""沙箱客户端(兼容层):实现已下沉到 common.clients.sandbox。

保留本模块是为了不打断既有 import 路径(agent_orchestrator.services.sandbox);
新代码请直接从 common.clients.sandbox 或 common.clients 引入。
"""

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
