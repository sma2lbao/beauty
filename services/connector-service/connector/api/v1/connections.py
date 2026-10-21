"""连接端点:同租户列表/详情/撤销;凭证只进不出(读端无凭证字段)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from connector.api.deps import get_session, get_tenant_id
from connector.schemas.connections import ConnectionRead
from connector.services import connections as connections_service

router = APIRouter(prefix="/connections", tags=["connections"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]


@router.get("", response_model=list[ConnectionRead])
async def list_connections(
    session: DBSession, tenant_id: TenantId
) -> list[ConnectionRead]:
    conns = await connections_service.list_connections(session, tenant_id)
    return [ConnectionRead.model_validate(conn) for conn in conns]


@router.get("/{connection_id}", response_model=ConnectionRead)
async def get_connection(
    connection_id: str, session: DBSession, tenant_id: TenantId
) -> ConnectionRead:
    conn = await connections_service.require_connection(
        session, tenant_id, connection_id
    )
    return ConnectionRead.model_validate(conn)


@router.delete("/{connection_id}", response_model=ConnectionRead)
async def revoke_connection(
    connection_id: str, session: DBSession, tenant_id: TenantId
) -> ConnectionRead:
    """撤销连接(留痕不物理删,§8);仅 active 可撤,重复撤销 409。"""
    conn = await connections_service.revoke_connection(
        session, tenant_id, connection_id
    )
    return ConnectionRead.model_validate(conn)
