"""沙箱执行器:按 scope_key 复用工作区沙箱,TTL 滑动。

tool-service 不在本机读写用户文件:所有文件工具都经 workspace-service 的
沙箱 exec 执行。同一对话/任务复用同一个沙箱,文件在多次工具调用之间可见;
多副本一致性由下游 (tenant_id, scope_key) 唯一约束兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from common.clients.sandbox import Sandbox, SandboxClient, SandboxExecResult
from common.security.token_broker import TokenBroker

from tool_service.constants import SANDBOX_TOOL_TTL_SECONDS
from tool_service.core.config import Settings
from tool_service.services.helpers import WORKSPACE_ROOT

#: scope_key 前缀:对话级优先,其次任务级,最后租户级兜底。
SCOPE_CONVERSATION_PREFIX = "conversation:"
SCOPE_RUN_PREFIX = "run:"
SCOPE_TENANT_PREFIX = "tenant:"
#: 服务身份(写进内部 JWT 的 subject,便于下游审计)。
SERVICE_SUBJECT = "svc-tool"


class SandboxProvider(Protocol):
    """tool-service 需要的沙箱能力(测试用假实现即可)。"""

    async def ensure(
        self, tenant_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str: ...

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = WORKSPACE_ROOT,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult: ...

    async def touch(self, tenant_id: str, sandbox_id: str) -> None: ...

    async def aclose(self) -> None: ...


def scope_key_for(
    tenant_id: str,
    *,
    sandbox_scope: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """决定沙箱复用粒度:显式 scope > 对话 > 任务 > 租户。"""

    if sandbox_scope:
        return sandbox_scope
    if conversation_id:
        return SCOPE_CONVERSATION_PREFIX + conversation_id
    if run_id:
        return SCOPE_RUN_PREFIX + run_id
    return SCOPE_TENANT_PREFIX + tenant_id


@dataclass(frozen=True, slots=True)
class HttpSandboxProvider:
    """基于 workspace-service HTTP API 的沙箱提供方。"""

    sandbox: Sandbox
    ttl_seconds: int = SANDBOX_TOOL_TTL_SECONDS

    async def ensure(
        self, tenant_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str:
        info, _created = await self.sandbox.ensure(
            tenant_id,
            scope_key=scope_key,
            ttl_seconds=self.ttl_seconds,
            run_id=run_id,
        )
        return info.id

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = WORKSPACE_ROOT,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        return await self.sandbox.exec(
            tenant_id,
            sandbox_id,
            argv,
            cwd=cwd,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
        )

    async def touch(self, tenant_id: str, sandbox_id: str) -> None:
        await self.sandbox.touch(tenant_id, sandbox_id)

    async def aclose(self) -> None:
        await self.sandbox.aclose()


class SandboxRuntime:
    """持有 TokenBroker + SandboxClient 的运行时,供 FastAPI lifespan 管理。"""

    def __init__(self, *, settings: Settings) -> None:
        self._broker = TokenBroker(
            base_url=settings.auth_issuer,
            internal_secret=settings.internal_secret,
            subject=SERVICE_SUBJECT,
        )
        self._client = SandboxClient(
            settings.workspace_service_url,
            timeout_seconds=settings.workspace_timeout_seconds,
            token_provider=self._broker.token,
        )
        self.provider = HttpSandboxProvider(
            sandbox=self._client,
            ttl_seconds=settings.sandbox_tool_ttl_seconds,
        )

    async def aclose(self) -> None:
        """先关沙箱客户端再关 broker(过期租约不阻塞退出)。"""

        await self._client.aclose()
        await self._broker.aclose()
