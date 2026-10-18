"""MCP Client 业务:注册表 CRUD、测试/刷新工具、工具规格生成(M1)。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.repositories import MCPServerRepository
from agent_orchestrator.schemas.mcp import (
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
    MCPTestRead,
    MCPToolRead,
)
from agent_orchestrator.services.mcp_client import MCPClient, MCPError
from agent_orchestrator.services.mcp_crypto import decrypt_token, encrypt_token
from agent_orchestrator.services.tools import ToolSpec


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_") or "server"


def _wrap_tool(tool: dict[str, Any]) -> MCPToolRead:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return MCPToolRead(
        name=str(tool.get("name") or ""),
        description=str(tool.get("description") or ""),
        input_schema=schema if isinstance(schema, dict) else {},
    )


def _to_read(server: MCPServer) -> MCPServerRead:
    return MCPServerRead(
        id=server.id,
        name=server.name,
        url=server.url,
        enabled=server.enabled,
        has_token=bool(server.encrypted_token),
        token_hint=server.token_hint,
        tools_allowlist=list(server.tools_allowlist or []),
        tools=[_wrap_tool(tool) for tool in server.tools_cache or []],
        timeout_seconds=server.timeout_seconds,
        last_status=server.last_status,
        last_checked_at=server.last_checked_at,
        last_error=server.last_error,
        created_at=server.created_at,
    )


def build_client(
    server: MCPServer,
    secret: str,
    *,
    transport=None,
) -> MCPClient:
    token = (
        decrypt_token(secret, server.encrypted_token)
        if server.encrypted_token
        else None
    )
    return MCPClient(
        server.url,
        token=token,
        timeout_seconds=server.timeout_seconds,
        transport=transport,
    )


async def list_servers(
    session: AsyncSession, tenant_id: str
) -> list[MCPServerRead]:
    servers = await MCPServerRepository(session).list_scoped(
        tenant_id, order_by=[MCPServer.created_at]
    )
    return [_to_read(server) for server in servers]


async def _require(
    session: AsyncSession, tenant_id: str, server_id: str
) -> MCPServer:
    return await MCPServerRepository(session).require_for_tenant(
        tenant_id, server_id
    )


async def create_server(
    session: AsyncSession, tenant_id: str, data: MCPServerCreate, *, secret: str
) -> MCPServerRead:
    repo = MCPServerRepository(session)
    if await repo.find_by_name(tenant_id, data.name) is not None:
        raise ConflictError("mcp.server_exists", "同名 MCP server 已存在")
    token = (data.token or "").strip()
    server = MCPServer(
        tenant_id=tenant_id,
        name=data.name,
        url=data.url,
        encrypted_token=encrypt_token(secret, token) if token else None,
        token_hint=token[-4:] if token else None,
        enabled=data.enabled,
        tools_allowlist=list(data.tools_allowlist),
        tools_cache=[],
        timeout_seconds=data.timeout_seconds,
    )
    await repo.add(server)
    return _to_read(server)


async def update_server(
    session: AsyncSession,
    tenant_id: str,
    server_id: str,
    data: MCPServerUpdate,
    *,
    secret: str,
) -> MCPServerRead:
    server = await _require(session, tenant_id, server_id)
    if data.name is not None:
        duplicate = await MCPServerRepository(session).find_by_name(
            tenant_id, data.name
        )
        if duplicate is not None and duplicate.id != server.id:
            raise ConflictError("mcp.server_exists", "同名 MCP server 已存在")
        server.name = data.name
    if data.url is not None:
        server.url = data.url
    if data.token is not None:
        token = data.token.strip()
        server.encrypted_token = encrypt_token(secret, token) if token else None
        server.token_hint = token[-4:] if token else None
    if data.enabled is not None:
        server.enabled = data.enabled
    if data.tools_allowlist is not None:
        server.tools_allowlist = list(data.tools_allowlist)
    if data.timeout_seconds is not None:
        server.timeout_seconds = data.timeout_seconds
    return _to_read(server)


async def delete_server(
    session: AsyncSession, tenant_id: str, server_id: str
) -> None:
    server = await _require(session, tenant_id, server_id)
    await MCPServerRepository(session).delete(server)


async def test_server(
    session: AsyncSession, tenant_id: str, server_id: str, *, secret: str
) -> MCPTestRead:
    server = await _require(session, tenant_id, server_id)
    client = build_client(server, secret)
    now = datetime.now(UTC)
    try:
        await client.initialize()
        raw_tools = await client.list_tools()
        tools = [_wrap_tool(tool) for tool in raw_tools]
        server.tools_cache = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in tools
        ]
        server.last_status = "ok"
        server.last_error = None
        server.last_checked_at = now
        return MCPTestRead(
            ok=True, detail=f"连接成功，发现 {len(tools)} 个工具", tools=tools
        )
    except (MCPError, Exception) as exc:  # noqa: BLE001 - 测试端点必须返回可读失败
        server.last_status = "error"
        server.last_error = f"{type(exc).__name__}: {exc}"[:500]
        server.last_checked_at = now
        return MCPTestRead(ok=False, detail=server.last_error, tools=[])
    finally:
        await client.aclose()


def _allowed(server: MCPServer, tool_name: str) -> bool:
    allowlist = list(server.tools_allowlist or [])
    return not allowlist or tool_name in allowlist


async def tool_specs(
    session: AsyncSession, tenant_id: str, *, secret: str
) -> list[ToolSpec]:
    """把已测试/启用的 MCP server 工具并入 ToolRegistry(命名空间隔离)。"""
    servers = await MCPServerRepository(session).list_scoped(
        tenant_id, MCPServer.enabled.is_(True), order_by=[MCPServer.created_at]
    )
    specs: list[ToolSpec] = []
    for server in servers:
        slug = _slug(server.name)
        for tool in server.tools_cache or []:
            wrapped = _wrap_tool(tool)
            if not wrapped.name or not _allowed(server, wrapped.name):
                continue
            specs.append(
                ToolSpec(
                    name=f"mcp__{slug}__{wrapped.name}",
                    description=wrapped.description or f"MCP {server.name} 工具",
                    parameters=wrapped.input_schema or {"type": "object"},
                    handler=_make_handler(server, wrapped.name, secret),
                    requires_confirmation=True,
                )
            )
    return specs


def _make_handler(server: MCPServer, tool_name: str, secret: str):
    async def handler(args: dict[str, Any]) -> str:
        client = build_client(server, secret)
        try:
            await client.initialize()
            return await client.call_tool(tool_name, args)
        except Exception as exc:  # noqa: BLE001 - 工具失败回喂错误文本
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            )
        finally:
            await client.aclose()

    return handler
