"""MCP Client 业务:注册表 CRUD、测试端点与工具装配入口。

两种传输共用同一张表与同一套读端契约,分支只出现在"怎么连"上:

- http:进程内 httpx(M1 现状,不动);
- stdio:命令在沙箱里拉起(P2),经 StdioSandboxPool 执行 probe。

安全口径:env/token 只存密文,读端只回 has_*/尾号/key 名;stdio 命令必须命中
白名单(能力即权限边界)。

连接/调用/handler 装配的实现见 services/mcp_runtime.py(R-29 后继拆分)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from common.errors import ConflictError, NotFoundError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.repositories import (
    MCPServerRepository,
)
from agent_orchestrator.services.mcp_runtime import (
    TRANSPORT_HTTP,
    TRANSPORT_STDIO,
    TokenProvider,
    _allowed,
    _call_tool_once,
    _make_http_handler,
    _make_stdio_handler,
    _provider_token,
    _slug,
    _stdio_request,
    _wrap_tool,
    build_client,
)
from agent_orchestrator.services.mcp_servers import (
    _decrypt_token_hint as _decrypt_token_hint,
)
from agent_orchestrator.services.mcp_servers import (
    _env_keys_for_update as _env_keys_for_update,
)
from agent_orchestrator.services.mcp_servers import _merged_payload as _merged_payload
from agent_orchestrator.services.mcp_servers import _oauth_fields as _oauth_fields
from agent_orchestrator.services.mcp_servers import _require as _require
from agent_orchestrator.services.mcp_servers import (
    _require_command_allowed as _require_command_allowed,
)
from agent_orchestrator.services.mcp_servers import _to_read as _to_read
from agent_orchestrator.services.mcp_servers import (
    _validation_message as _validation_message,
)
from agent_orchestrator.services.mcp_servers import create_server as create_server
from agent_orchestrator.services.mcp_servers import delete_server as delete_server
from agent_orchestrator.services.mcp_servers import list_servers as list_servers
from agent_orchestrator.services.mcp_servers import reference_note as reference_note
from agent_orchestrator.services.mcp_servers import test_server as test_server
from agent_orchestrator.services.mcp_servers import update_server as update_server
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.tools import ToolRisk, ToolSpec

logger = get_logger(__name__)

#: 对外保留的实现(历史调用方/测试按 mcp.<name> 取用);实现细节已移入 mcp_runtime。
#: 下划线名的几项是 mcp_runtime 的机制函数,仅测试直取,不对外。
__all__ = [
    "TRANSPORT_HTTP",
    "TRANSPORT_STDIO",
    "_allowed",
    "_call_tool_once",
    "_make_http_handler",
    "_make_stdio_handler",
    "_provider_token",
    "_stdio_request",
    "build_client",
    "create_server",
    "delete_server",
    "list_servers",
    "reference_note",
    "resolve_selected",
    "test_server",
    "tool_specs",
    "update_server",
]


async def tool_specs(
    session: AsyncSession,
    tenant_id: str,
    *,
    secret: str,
    pool: StdioSandboxPool | None = None,
    on_auth_required: Callable[[str, str], Awaitable[None]] | None = None,
    token_provider: TokenProvider | None = None,
    server_ids: Sequence[str] | None = None,
) -> list[ToolSpec]:
    """把已测试/启用的 MCP server 工具并入 ToolRegistry(命名空间隔离)。

    tools_cache 只由 test 端点刷新:P2 不在每次 run 前重新 list(冷启动代价高);
    P4 再做 tools_cache 的哈希校验(TODO)。

    server_ids 非空 = 用户显式引用了这些 server(§6.7):只装配它们;
    None / 空序列维持现状(全部已启用的 server)。
    """
    servers = await MCPServerRepository(session).list_scoped(
        tenant_id, MCPServer.enabled.is_(True), order_by=[MCPServer.created_at]
    )
    if server_ids:
        wanted = set(server_ids)
        servers = [server for server in servers if server.id in wanted]
    specs: list[ToolSpec] = []
    for server in servers:
        stdio = (server.transport or TRANSPORT_HTTP) == TRANSPORT_STDIO
        if stdio and pool is None:
            logger.warning(
                "stdio MCP 已跳过:沙箱未启用(mcp_stdio_enabled/sandbox_enabled)",
                server=server.name,
            )
            continue
        slug = _slug(server.name)
        for tool in server.tools_cache or []:
            wrapped = _wrap_tool(tool)
            if not wrapped.name or not _allowed(server, wrapped.name):
                continue
            handler = (
                _make_stdio_handler(tenant_id, server, wrapped.name, secret, pool)
                if stdio
                else _make_http_handler(
                    server,
                    wrapped.name,
                    secret,
                    on_auth_required,
                    token_provider,
                )
            )
            specs.append(
                ToolSpec(
                    name=f"mcp__{slug}__{wrapped.name}",
                    description=wrapped.description or f"MCP {server.name} 工具",
                    parameters=wrapped.input_schema or {"type": "object"},
                    handler=handler,
                    # 未信任 server 的工具可能对外产生不可逆副作用 → DANGER;
                    # 用户开了 auto_approve(信任该 server)→ WRITE,自动执行。
                    # 对话路径没有审批流,所以 DANGER 工具在 chat 里一律不执行。
                    risk=(ToolRisk.WRITE if server.auto_approve else ToolRisk.DANGER),
                )
            )
    return specs


async def resolve_selected(
    session: AsyncSession, tenant_id: str, server_ids: Sequence[str]
) -> list[MCPServer]:
    """解析用户显式引用的 MCP server:去重保序,未知 404、停用 409。

    在消息落库前调用:引用不合法就整条消息不落,不留半残引用。
    """
    unique = list(dict.fromkeys(server_ids))
    if not unique:
        return []
    servers = await MCPServerRepository(session).list_scoped(
        tenant_id, MCPServer.id.in_(unique)
    )
    by_id = {server.id: server for server in servers}
    missing = [server_id for server_id in unique if server_id not in by_id]
    if missing:
        raise NotFoundError(
            "mcp.server_not_found",
            "MCP server 不存在",
            details={"mcp_server_ids": missing},
        )
    selected = [by_id[server_id] for server_id in unique]
    disabled = [server for server in selected if not server.enabled]
    if disabled:
        raise ConflictError(
            "mcp.server_disabled",
            f"MCP server 已停用:{'、'.join(server.name for server in disabled)}",
            details={"mcp_server_ids": [server.id for server in disabled]},
        )
    return selected
