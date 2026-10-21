"""连接业务逻辑:凭证只进不出;状态守卫与租户隔离同 orchestrator 约定。

数据访问经 connector.repositories(租户过滤/404 键名由仓储声明)。
当前无内置连接器——连接行只来自历史数据或未来接入的 provider,本模块
只做读取与撤销。
"""

from __future__ import annotations

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from connector.models.connections import Connection
from connector.repositories import ConnectionRepository
from connector.schemas.connections import ConnectionStatus


async def require_connection(
    session: AsyncSession, tenant_id: str, connection_id: str
) -> Connection:
    """取租户内连接;不存在 → 404。"""
    return await ConnectionRepository(session).require_for_tenant(
        tenant_id, connection_id
    )


def _require_active(conn: Connection) -> None:
    """只有 active 连接可撤销;撤销后一律 409。"""
    if conn.status != ConnectionStatus.ACTIVE.value:
        raise ConflictError(
            "connector.invalid_state",
            f"连接状态 {conn.status} 不允许该操作",
            details={"connection_id": conn.id, "status": conn.status},
        )


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
