"""连接器仓储:租户过滤与 404 语义在此声明一次(§8)。

凭证字段只进不出——仓储层面同样不做任何"顺手返回凭证明文"的便捷方法。
"""

from __future__ import annotations

from database.repository import TenantRepository

from connector.models.connections import Connection


class ConnectionRepository(TenantRepository[Connection]):
    model = Connection
    not_found_code = "connector.connection_not_found"
    not_found_message = "连接不存在"
    id_detail_key = "connection_id"

    async def find_by_account(
        self, tenant_id: str, *, provider: str, account: str
    ) -> Connection | None:
        """同租户内按 (provider, account) 找已存在的连接(创建时判重)。"""
        found = await self.list_scoped(
            tenant_id,
            Connection.provider == provider,
            Connection.account == account,
        )
        return found[0] if found else None
