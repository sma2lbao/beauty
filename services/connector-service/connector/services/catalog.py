"""目录:给前端展示「有哪些连接器/哪些已连接」,给 tool-service 装配工具。

工具 descriptor 复用 common.tools 的线协议(executor=connector),执行面仍是
tool-service,审批/审计沿用既有机制(设计 §6)。
"""

from __future__ import annotations

from typing import Any

from common.tools import ExecutorKind, ToolDescriptor, ToolKind
from sqlalchemy.ext.asyncio import AsyncSession

from connector.core.config import Settings
from connector.models.connections import Connection
from connector.providers import enabled_provider_names, require_provider
from connector.repositories import ConnectionRepository
from connector.schemas.connectors import (
    ConnectionBrief,
    ConnectorsRead,
    ProviderInfo,
    ToolInfo,
)

_ACTIVE = "active"


def _latest_by_provider(connections: list[Connection]) -> dict[str, Connection]:
    """每个 provider 取最近一条(任意状态),用于展示「已连接/已失效」。"""
    latest: dict[str, Connection] = {}
    for conn in connections:
        latest.setdefault(conn.provider, conn)
    return latest


def _usable(conn: Connection | None) -> bool:
    return bool(conn and conn.status == _ACTIVE and conn.enabled)


def _brief(conn: Connection) -> ConnectionBrief:
    return ConnectionBrief(
        id=conn.id,
        status=conn.status,
        account=conn.account,
        display_name=conn.display_name,
        enabled=conn.enabled,
        expires_at=conn.expires_at,
        last_checked_at=conn.last_checked_at,
        last_error_code=conn.last_error_code,
    )


async def list_providers(
    session: AsyncSession, settings: Settings, tenant_id: str
) -> ConnectorsRead:
    connections = await ConnectionRepository(session).list_filtered(tenant_id)
    latest = _latest_by_provider(connections)
    providers: list[ProviderInfo] = []
    for name in enabled_provider_names(settings):
        provider = require_provider(settings, name)
        conn = latest.get(provider.name)
        providers.append(
            ProviderInfo(
                name=provider.name,
                display_name=provider.display_name,
                description=provider.description,
                auth_kind=provider.auth_kind,
                scopes=list(provider.scopes()),
                docs_url=provider.docs_url,
                configured=provider.configured,
                connected=_usable(conn),
                connection=_brief(conn) if conn is not None else None,
                tools=[
                    ToolInfo(
                        name=provider.full_tool_name(spec.name),
                        title=spec.title,
                        description=spec.description,
                        risk=spec.risk.value,
                    )
                    for spec in provider.tools
                ],
            )
        )
    return ConnectorsRead(providers=providers)


async def tool_descriptors(
    session: AsyncSession, settings: Settings, tenant_id: str
) -> list[ToolDescriptor]:
    """只返回「有 active+enabled 连接」的 provider 的工具(租户隔离)。"""
    connections = await ConnectionRepository(session).list_filtered(
        tenant_id, status=_ACTIVE, enabled=True
    )
    connected = {conn.provider for conn in connections}
    descriptors: list[ToolDescriptor] = []
    for name in enabled_provider_names(settings):
        if name not in connected:
            continue
        provider = require_provider(settings, name)
        for spec in provider.tools:
            descriptors.append(
                ToolDescriptor(
                    name=provider.full_tool_name(spec.name),
                    description=f"[{provider.display_name}] {spec.description}",
                    input_schema=spec.input_schema,
                    output_schema=spec.output_schema,
                    risk=spec.risk,
                    kind=ToolKind.FUNCTION,
                    executor=ExecutorKind.CONNECTOR,
                    timeout_seconds=30.0,
                    scopes=provider.scopes(),
                    # 执行面按 provider 寻址(tool-service 看不到 connection_id),
                    # 这个字段就是 tool-service 路由执行请求的唯一依据。
                    connector={"provider": provider.name},
                )
            )
    return descriptors


async def tool_descriptor_payloads(
    session: AsyncSession, settings: Settings, tenant_id: str
) -> dict[str, Any]:
    tools = await tool_descriptors(session, settings, tenant_id)
    return {"tools": [descriptor.to_payload() for descriptor in tools]}
