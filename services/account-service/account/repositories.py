"""账号域仓储:租户过滤与 404 语义在这里声明一次,业务层不再手写。

- `Organization` 的 id 本身就是租户标识,所以用无租户语义的 `Repository`;
- 成员、会话与刷新令牌是租户业务表,用 `TenantRepository`(读取自动带 tenant_id);
- `find_active_by_email` 是唯一的跨租户读:登录入口没有租户上下文,
  方法名显式声明这个例外(当前约定 email 全局唯一,注册时查重保证)。
"""

from database.repository import Repository, TenantRepository
from sqlalchemy import select

from account.models.account import (
    BYOKKey,
    OAuthIdentity,
    Organization,
    RefreshToken,
    Session,
    UsageEvent,
    User,
)


class OrganizationRepository(Repository[Organization]):
    model = Organization
    not_found_code = "account.organization_not_found"
    not_found_message = "组织不存在"


class UserRepository(TenantRepository[User]):
    model = User
    not_found_code = "account.user_not_found"
    not_found_message = "成员不存在"
    id_detail_key = "user_id"

    async def find_active_by_email(self, email: str) -> User | None:
        """按 email 跨租户找可登录成员(登录入口的协议例外,见模块说明)。"""
        statement = (
            select(User)
            .where(User.email == email, User.status == "active")
            .order_by(User.created_at, User.id)
            .limit(1)
        )
        return await self.session.scalar(statement)


class SessionRepository(TenantRepository[Session]):
    model = Session
    not_found_code = "account.session_not_found"
    not_found_message = "会话不存在"
    id_detail_key = "session_id"


class OAuthIdentityRepository(TenantRepository[OAuthIdentity]):
    model = OAuthIdentity
    not_found_code = "account.oauth_identity_not_found"
    not_found_message = "社交登录身份不存在"
    id_detail_key = "oauth_identity_id"

    async def find_by_provider_subject(
        self, provider: str, subject: str
    ) -> OAuthIdentity | None:
        """登录入口无租户上下文:provider + subject 是外部身份的唯一键。"""
        return await self.session.scalar(
            select(OAuthIdentity).where(
                OAuthIdentity.provider == provider,
                OAuthIdentity.subject == subject,
            )
        )


class UsageEventRepository(TenantRepository[UsageEvent]):
    model = UsageEvent
    not_found_code = "usage.event_not_found"
    not_found_message = "用量事件不存在"
    id_detail_key = "usage_event_id"

    async def list_since(self, tenant_id: str, since) -> list[UsageEvent]:
        return await self.list_scoped(
            tenant_id,
            UsageEvent.created_at >= since,
            order_by=[UsageEvent.created_at],
        )


class BYOKRepository(TenantRepository[BYOKKey]):
    model = BYOKKey
    not_found_code = "byok.not_found"
    not_found_message = "BYOK 密钥不存在"
    id_detail_key = "provider"

    async def find_for_provider(
        self, tenant_id: str, provider: str
    ) -> BYOKKey | None:
        return await self.session.scalar(
            self.scoped(tenant_id, BYOKKey.provider == provider)
        )


class RefreshTokenRepository(TenantRepository[RefreshToken]):
    model = RefreshToken
    not_found_code = "account.refresh_not_found"
    not_found_message = "刷新令牌不存在"
    id_detail_key = "refresh_token_id"

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """按哈希取令牌(刷新入口无租户上下文,哈希即 256 位随机凭据)。"""
        return await self.session.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
