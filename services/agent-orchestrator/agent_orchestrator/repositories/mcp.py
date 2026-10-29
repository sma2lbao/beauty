"""MCP server 与其 OAuth 授权行仓储(按域拆分)。"""

from __future__ import annotations

from collections.abc import Sequence

from database.repository import TenantRepository

from agent_orchestrator.models.mcp import MCPServer, MCPServerAuth


class MCPServerRepository(TenantRepository[MCPServer]):
    model = MCPServer
    not_found_code = "mcp.server_not_found"
    not_found_message = "MCP server 不存在"
    id_detail_key = "mcp_server_id"

    async def find_by_name(self, tenant_id: str, name: str) -> MCPServer | None:
        return await self.session.scalar(self.scoped(tenant_id, MCPServer.name == name))


class MCPServerAuthRepository(TenantRepository[MCPServerAuth]):
    """MCP server 的浏览器 OAuth 授权行(与 server 一对一)。"""

    model = MCPServerAuth
    not_found_code = "mcp.auth_not_found"
    not_found_message = "该 MCP server 没有授权记录"
    id_detail_key = "mcp_server_id"

    async def get_for_server(
        self, tenant_id: str, server_id: str
    ) -> MCPServerAuth | None:
        return await self.session.scalar(
            self.scoped(tenant_id, MCPServerAuth.server_id == server_id)
        )

    async def list_for_servers(
        self, tenant_id: str, server_ids: Sequence[str]
    ) -> dict[str, MCPServerAuth]:
        """列表页一次取回所有授权行,避免 N+1。"""
        if not server_ids:
            return {}
        rows = await self.session.scalars(
            self.scoped(tenant_id, MCPServerAuth.server_id.in_(list(server_ids)))
        )
        return {row.server_id: row for row in rows}
