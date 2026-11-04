"""MCP Client 端点(M1 本地版 + P2 stdio)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_mcp_stdio_pool,
    get_session,
    get_settings,
    get_tenant_id,
)
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.mcp import (
    MCPOAuthStartRead,
    MCPOAuthStartRequest,
    MCPPresetListRead,
    MCPPresetRead,
    MCPServerCreate,
    MCPServerRead,
    MCPServerUpdate,
    MCPTestRead,
)
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import mcp_oauth, mcp_presets, mcp_servers
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool

router = APIRouter(prefix="/mcp/servers", tags=["mcp"])
presets_router = APIRouter(prefix="/mcp/presets", tags=["mcp"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
PoolDep = Annotated[StdioSandboxPool | None, Depends(get_mcp_stdio_pool)]


@presets_router.get("", response_model=MCPPresetListRead)
async def list_presets() -> MCPPresetListRead:
    """内置 MCP 预设清单(前端据此渲染"预设连接器"卡片)。"""
    return MCPPresetListRead(
        presets=[
            MCPPresetRead(
                key=preset.key,
                name=preset.name,
                url=preset.url,
                description=preset.description,
                docs_url=preset.docs_url,
            )
            for preset in mcp_presets.list_presets()
        ]
    )


@presets_router.post("/{key}", response_model=MCPServerRead)
async def ensure_preset(
    key: str, session: DBSession, tenant_id: TenantId, settings: SettingsDep
) -> MCPServerRead:
    """一键连接:幂等 ensure 该预设的 MCP server(已建过就直接返回)。"""
    return await mcp_servers.ensure_preset_server(
        session,
        tenant_id,
        key,
        secret=settings.mcp_secret,
        allowlist=settings.mcp_stdio_command_allowlist,
    )


@router.get("", response_model=list[MCPServerRead])
async def list_servers(
    session: DBSession, tenant_id: TenantId, settings: SettingsDep
) -> list[MCPServerRead]:
    return await mcp_service.list_servers(
        session, tenant_id, secret=settings.mcp_secret
    )


@router.post("", response_model=MCPServerRead, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: MCPServerCreate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> MCPServerRead:
    return await mcp_service.create_server(
        session,
        tenant_id,
        body,
        secret=settings.mcp_secret,
        allowlist=settings.mcp_stdio_command_allowlist,
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
        session,
        tenant_id,
        server_id,
        body,
        secret=settings.mcp_secret,
        allowlist=settings.mcp_stdio_command_allowlist,
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
    pool: PoolDep,
) -> MCPTestRead:
    return await mcp_service.test_server(
        session,
        tenant_id,
        server_id,
        secret=settings.mcp_secret,
        pool=pool,
        settings=settings,
    )


@router.post("/{server_id}/oauth/start", response_model=MCPOAuthStartRead)
async def start_oauth(
    server_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
    body: MCPOAuthStartRequest | None = None,
) -> MCPOAuthStartRead:
    """生成浏览器授权链接(不跳转,由前端整页导航到 authorize_url)。

    body.next 指定授权完成后回到哪个站内页面(/mcp 或 /connectors),默认 /mcp;
    next 会被写进签名 state,回调时据此生成 redirect_path。
    """
    authorize_url = await mcp_oauth.start_authorization(
        session,
        tenant_id,
        server_id,
        settings=settings,
        secret=settings.mcp_secret,
        next_path=body.next if body is not None else None,
    )
    return MCPOAuthStartRead(
        authorize_url=authorize_url,
        expires_in=settings.mcp_oauth_state_ttl_seconds,
    )


@router.post("/{server_id}/oauth/disconnect", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_oauth(
    server_id: str, session: DBSession, tenant_id: TenantId
) -> None:
    """解除授权:删除本地授权行(不调用远端 revoke)。"""
    await mcp_oauth.disconnect(session, tenant_id, server_id)
