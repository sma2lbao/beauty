"""连接器仓储:租户过滤与 404 语义在此声明一次(§8)。

凭证字段只进不出——仓储层面同样不做任何"顺手返回凭证明文"的便捷方法。
"""

from __future__ import annotations

from database.repository import TenantRepository
from sqlalchemy import Select

from connector.models.connections import Connection
from connector.models.oauth import OAuthSession
from connector.models.tenant_apps import TenantApp


class ConnectionRepository(TenantRepository[Connection]):
    model = Connection
    not_found_code = "connector.connection_not_found"
    not_found_message = "连接不存在"
    id_detail_key = "connection_id"

    async def list_filtered(
        self,
        tenant_id: str,
        *,
        provider: str | None = None,
        status: str | None = None,
        enabled: bool | None = None,
    ) -> list[Connection]:
        criteria = []
        if provider:
            criteria.append(Connection.provider == provider)
        if status:
            criteria.append(Connection.status == status)
        if enabled is not None:
            criteria.append(Connection.enabled.is_(enabled))
        return await self.list_scoped(
            tenant_id,
            *criteria,
            order_by=[Connection.created_at],
        )

    async def latest_active(self, tenant_id: str, provider: str) -> Connection | None:
        """执行面按租户解析连接:active + enabled,多条取最近创建的一条。"""
        statement = (
            self.scoped(
                tenant_id,
                Connection.provider == provider,
                Connection.status == "active",
                Connection.enabled.is_(True),
            )
            .order_by(Connection.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def find_by_account(
        self, tenant_id: str, provider: str, account: str
    ) -> Connection | None:
        return await self.session.scalar(
            self.scoped(
                tenant_id,
                Connection.provider == provider,
                Connection.account == account,
            )
        )

    async def require_for_update(
        self, tenant_id: str, connection_id: str
    ) -> Connection:
        """行锁读取:刷新单飞靠它把「检查过期 -> 换 token -> 写回」串成一次。"""
        statement: Select[tuple[Connection]] = self.scoped(
            tenant_id, Connection.id == connection_id
        ).with_for_update()
        conn = await self.session.scalar(statement)
        if conn is None:
            raise self._not_found(connection_id)
        return conn


class OAuthSessionRepository(TenantRepository[OAuthSession]):
    model = OAuthSession
    not_found_code = "connector.oauth_session_not_found"
    not_found_message = "授权会话不存在"
    id_detail_key = "session_id"

    async def get_by_state(self, state_nonce: str) -> OAuthSession | None:
        """回调按 nonce 查会话(此时还没有用户身份,只能全局查)。"""
        return await self.session.scalar(
            self.select(OAuthSession.state_nonce == state_nonce)
        )


class TenantAppRepository(TenantRepository[TenantApp]):
    model = TenantApp
    not_found_code = "connector.tenant_app_not_found"
    not_found_message = "租户应用凭证不存在"
    id_detail_key = "app_id"

    async def get_for_provider(self, tenant_id: str, provider: str) -> TenantApp | None:
        return await self.session.scalar(
            self.scoped(tenant_id, TenantApp.provider == provider)
        )
