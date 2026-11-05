"""账号域仓储:账号过滤与 404 语义在这里声明一次,业务层不再手写。

- `UserRepository` 用无账号语义的 `Repository`(users 自身即账号主体);
- 其余账号私有表用 `AccountRepository`(读取自动带 account_id,跨账号 404);
- 少数"登录入口没有账号上下文"的读(按 email / 外部身份 / token 哈希)以显式
  命名方法留在各自仓储,用 `select` 逃生口,方法名声明这个例外。
"""

from database.repository import AccountRepository, Repository
from sqlalchemy import select

from account.models.account import (
    ApiKey,
    BYOKKey,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthIdentity,
    OAuthToken,
    RefreshToken,
    Session,
    UsageEvent,
    User,
)


class UserRepository(Repository[User]):
    model = User
    not_found_code = "account.account_not_found"
    not_found_message = "账号不存在"
    id_detail_key = "account_id"

    async def find_active_by_email(self, email: str) -> User | None:
        """按 email 找可登录账号(登录入口的协议例外,见模块说明)。"""
        statement = (
            select(User)
            .where(User.email == email, User.status == "active")
            .order_by(User.created_at, User.id)
            .limit(1)
        )
        return await self.session.scalar(statement)

    async def find_by_email(self, email: str) -> User | None:
        """按 email 找账号(注册查重用;大小写已由调用方归一)。"""
        return await self.session.scalar(
            select(User).where(User.email == email).limit(1)
        )


class SessionRepository(AccountRepository[Session]):
    model = Session
    not_found_code = "account.session_not_found"
    not_found_message = "会话不存在"
    id_detail_key = "session_id"


class OAuthIdentityRepository(AccountRepository[OAuthIdentity]):
    model = OAuthIdentity
    not_found_code = "account.oauth_identity_not_found"
    not_found_message = "社交登录身份不存在"
    id_detail_key = "oauth_identity_id"

    async def find_by_provider_subject(
        self, provider: str, subject: str
    ) -> OAuthIdentity | None:
        """登录入口无账号上下文:provider + subject 是外部身份的唯一键。"""
        return await self.session.scalar(
            self.select(
                OAuthIdentity.provider == provider,
                OAuthIdentity.subject == subject,
            )
        )


class OAuthClientRepository(Repository[OAuthClient]):
    """OAuth 客户端是全局表(授权时才绑定账号);按 client_id 查。"""

    model = OAuthClient
    not_found_code = "oauth.client_not_found"
    not_found_message = "OAuth 客户端不存在"
    id_detail_key = "client_id"

    async def find(self, client_id: str) -> OAuthClient | None:
        return await self.session.scalar(
            select(OAuthClient).where(OAuthClient.client_id == client_id)
        )


class OAuthCodeRepository(AccountRepository[OAuthAuthorizationCode]):
    model = OAuthAuthorizationCode
    not_found_code = "oauth.code_not_found"
    not_found_message = "授权码不存在"
    id_detail_key = "code"

    async def find_by_hash(self, code_hash: str) -> OAuthAuthorizationCode | None:
        return await self.session.scalar(
            self.select(OAuthAuthorizationCode.code_hash == code_hash)
        )


class OAuthTokenRepository(AccountRepository[OAuthToken]):
    model = OAuthToken
    not_found_code = "oauth.token_not_found"
    not_found_message = "OAuth token 不存在"
    id_detail_key = "token"

    async def find_by_access_hash(self, token_hash: str) -> OAuthToken | None:
        return await self.session.scalar(
            self.select(OAuthToken.access_hash == token_hash)
        )

    async def find_by_refresh_hash(self, token_hash: str) -> OAuthToken | None:
        return await self.session.scalar(
            self.select(OAuthToken.refresh_hash == token_hash)
        )

    async def list_active_for_account(self, account_id: str) -> list[OAuthToken]:
        return await self.list_scoped(account_id, OAuthToken.revoked_at.is_(None))


class ApiKeyRepository(AccountRepository[ApiKey]):
    model = ApiKey
    not_found_code = "api_key.not_found"
    not_found_message = "API Key 不存在"
    id_detail_key = "api_key_id"

    async def find_by_hash(self, key_hash: str) -> ApiKey | None:
        return await self.session.scalar(self.select(ApiKey.key_hash == key_hash))


class UsageEventRepository(AccountRepository[UsageEvent]):
    model = UsageEvent
    not_found_code = "usage.event_not_found"
    not_found_message = "用量事件不存在"
    id_detail_key = "usage_event_id"

    async def list_since(self, account_id: str, since) -> list[UsageEvent]:
        return await self.list_scoped(
            account_id,
            UsageEvent.created_at >= since,
            order_by=[UsageEvent.created_at],
        )


class BYOKRepository(AccountRepository[BYOKKey]):
    model = BYOKKey
    not_found_code = "byok.not_found"
    not_found_message = "BYOK 密钥不存在"
    id_detail_key = "provider"

    async def find_for_provider(self, account_id: str, provider: str) -> BYOKKey | None:
        return await self.session.scalar(
            self.scoped(account_id, BYOKKey.provider == provider)
        )


class RefreshTokenRepository(AccountRepository[RefreshToken]):
    model = RefreshToken
    not_found_code = "account.refresh_not_found"
    not_found_message = "刷新令牌不存在"
    id_detail_key = "refresh_token_id"

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """按哈希取令牌(刷新入口无账号上下文,哈希即 256 位随机凭据)。"""
        return await self.session.scalar(
            self.select(RefreshToken.token_hash == token_hash)
        )
