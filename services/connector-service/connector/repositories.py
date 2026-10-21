"""连接器仓储:租户过滤与 404 语义在此声明一次(§8)。

凭证字段只进不出——仓储层面同样不做任何"顺手返回凭证明文"的便捷方法。
当前无内置 provider,仓储只保留框架所需的通用能力。
"""

from __future__ import annotations

from database.repository import TenantRepository

from connector.models.connections import Connection


class ConnectionRepository(TenantRepository[Connection]):
    model = Connection
    not_found_code = "connector.connection_not_found"
    not_found_message = "连接不存在"
    id_detail_key = "connection_id"
