"""连接器仓储:账号过滤与 404 语义在此声明一次(§8)。

凭证字段只进不出——仓储层面同样不做任何"顺手返回凭证明文"的便捷方法。
"""

from __future__ import annotations

from datetime import datetime

from database.repository import AccountRepository
from sqlalchemy import Select, or_, select

from connector.models.account_apps import AccountApp
from connector.models.connections import Connection
from connector.models.oauth import OAuthSession


class ConnectionRepository(AccountRepository[Connection]):
    model = Connection
    not_found_code = "connector.connection_not_found"
    not_found_message = "连接不存在"
    id_detail_key = "connection_id"

    async def list_filtered(
        self,
        account_id: str,
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
            account_id,
            *criteria,
            order_by=[Connection.created_at],
        )

    async def latest_active(self, account_id: str, provider: str) -> Connection | None:
        """执行面按账号解析连接:active + enabled,多条取最近创建的一条。"""
        statement = (
            self.scoped(
                account_id,
                Connection.provider == provider,
                Connection.status == "active",
                Connection.enabled.is_(True),
            )
            .order_by(Connection.created_at.desc())
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def list_stale_active(self, stale_before: datetime) -> list[Connection]:
        """巡检用(跨账号):active + enabled 且「从未成功刷新」或上次刷新早于阈值。

        跨账号是刻意为之:巡检是系统任务,不归属任何请求账号;逐条刷新时再按
        行上的 account_id 回到账号作用域(§5.5 的每 24h 巡检)。
        """
        statement = (
            select(Connection)
            .where(
                Connection.status == "active",
                Connection.enabled.is_(True),
                or_(
                    Connection.last_refreshed_at.is_(None),
                    Connection.last_refreshed_at < stale_before,
                ),
            )
            .order_by(Connection.created_at)
        )
        return list((await self.session.scalars(statement)).all())

    async def find_by_account(
        self, account_id: str, provider: str, account: str
    ) -> Connection | None:
        return await self.session.scalar(
            self.scoped(
                account_id,
                Connection.provider == provider,
                Connection.account == account,
            )
        )

    async def require_for_update(
        self, account_id: str, connection_id: str
    ) -> Connection:
        """行锁读取:刷新单飞靠它把「检查过期 -> 换 token -> 写回」串成一次。"""
        statement: Select[tuple[Connection]] = self.scoped(
            account_id, Connection.id == connection_id
        ).with_for_update()
        conn = await self.session.scalar(statement)
        if conn is None:
            raise self._not_found(connection_id)
        return conn


class OAuthSessionRepository(AccountRepository[OAuthSession]):
    model = OAuthSession
    not_found_code = "connector.oauth_session_not_found"
    not_found_message = "授权会话不存在"
    id_detail_key = "session_id"

    async def get_by_state(self, state_nonce: str) -> OAuthSession | None:
        """回调按 nonce 查会话(此时还没有用户身份,只能全局查)。"""
        return await self.session.scalar(
            self.select(OAuthSession.state_nonce == state_nonce)
        )


class AccountAppRepository(AccountRepository[AccountApp]):
    model = AccountApp
    not_found_code = "connector.account_app_not_found"
    not_found_message = "账号应用凭证不存在"
    id_detail_key = "app_id"

    async def get_for_provider(
        self, account_id: str, provider: str
    ) -> AccountApp | None:
        return await self.session.scalar(
            self.scoped(account_id, AccountApp.provider == provider)
        )
