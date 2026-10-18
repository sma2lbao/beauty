"""MCP Client 端点(M1 本地版)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_settings, get_tenant_id
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.mcp import (
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
    MCPTestRead,
)
from agent_orchestrator.services import mcp as mcp_service

router = APIRouter(prefix="/mcp/servers", tags=["mcp"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("", response_model=list[MCPServerRead])
async def list_servers(session: DBSession, tenant_id: TenantId) -> list[MCPServerRead]:
    return await mcp_service.list_servers(session, tenant_id)


@router.post("", response_model=MCPServerRead, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: MCPServerCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> MCPServerRead:
    return await mcp_service.create_server(
        session, tenant_id, body, secret=settings.mcp_secret
    )


@router.patch("/{server_id}", response_model=MCPServerRead)
async def update_server(
    server_id: str,
    body: MCPServerUpdate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> MCPServerRead:
    return await mcp_service.update_server(
        session, tenant_id, server_id, body, secret=settings.mcp_secret
    )


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str, session: DBSession, tenant_id: TenantId
) -> None:
    await mcp_service.delete_server(session, tenant_id, server_id)


@router.post("/{server_id}/test", response_model=MCPTestRead)
async def test_server(
    server_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> MCPTestRead:
    return await mcp_service.test_server(
        session, tenant_id, server_id, secret=settings.mcp_secret
    )
