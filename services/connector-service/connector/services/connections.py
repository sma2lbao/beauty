"""连接业务逻辑:凭证只进不出;状态守卫与租户隔离同 orchestrator 约定。

数据访问经 `connector.repositories`(租户过滤/404 键名由仓储声明)。
"""

from __future__ import annotations

from datetime import UTC, datetime

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models.connections import Connection
from connector.repositories import ConnectionRepository
from connector.schemas.connections import ConnectionCreate, ConnectionStatus


async def require_connection(
    session: AsyncSession, tenant_id: str, connection_id: str
) -> Connection:
    """取租户内连接;不存在 → 404。"""
    return await ConnectionRepository(session).require_for_tenant(
        tenant_id, connection_id
    )


def _require_active(conn: Connection) -> None:
    """只有 active 连接可测试/收取;撤销后一律 409。"""
    if conn.status != ConnectionStatus.ACTIVE.value:
        raise ConflictError(
            "connector.invalid_state",
            f"连接状态 {conn.status} 不允许该操作",
            details={"connection_id": conn.id, "status": conn.status},
        )


async def require_active_connection(
    session: AsyncSession, tenant_id: str, connection_id: str
) -> Connection:
    """取连接并要求 active(探活/收取共用的守卫入口)。"""
    conn = await require_connection(session, tenant_id, connection_id)
    _require_active(conn)
    return conn


async def create_connection(
    session: AsyncSession, tenant_id: str, data: ConnectionCreate
) -> Connection:
    connections = ConnectionRepository(session)
    duplicate = await connections.find_by_account(
        tenant_id, provider=data.provider, account=data.account
    )
    if duplicate is not None:
        raise ConflictError(
            "connector.connection_exists",
            "该账号连接已存在",
            details={"connection_id": duplicate.id, "account": duplicate.account},
        )
    conn = Connection(
        tenant_id=tenant_id,
        provider=data.provider,
        account=data.account,
        display_name=data.display_name,
        status=ConnectionStatus.ACTIVE.value,
        credentials=data.credentials.model_dump(),
    )
    return await connections.add(conn)


async def list_connections(session: AsyncSession, tenant_id: str) -> list[Connection]:
    return await ConnectionRepository(session).list_scoped(
        tenant_id, order_by=[Connection.created_at]
    )


async def revoke_connection(
    session: AsyncSession, tenant_id: str, connection_id: str
) -> Connection:
    """撤销而非物理删除:凭证与审计需要留痕(§8)。"""
    connections = ConnectionRepository(session)
    conn = await connections.require_for_tenant(tenant_id, connection_id)
    _require_active(conn)
    conn.status = ConnectionStatus.REVOKED.value
    return await connections.add(conn)


def mark_checked(conn: Connection) -> None:
    """连通性测试后刷新 last_checked_at(成功与否都算一次探测)。"""
    conn.last_checked_at = datetime.now(UTC)
