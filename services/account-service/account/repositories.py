"""账号域仓储:租户过滤与 404 语义在这里声明一次,业务层不再手写。

- `Organization` 的 id 本身就是租户标识,所以用无租户语义的 `Repository`;
- 成员与会话是租户业务表,用 `TenantRepository`(读取自动带 tenant_id)。
"""

from database.repository import Repository, TenantRepository

from account.models.account import Organization, Session, User


class OrganizationRepository(Repository[Organization]):
    model = Organization
    not_found_code = "account.organization_not_found"
    not_found_message = "组织不存在"


class UserRepository(TenantRepository[User]):
    model = User
    not_found_code = "account.user_not_found"
    not_found_message = "成员不存在"
    id_detail_key = "user_id"


class SessionRepository(TenantRepository[Session]):
    model = Session
    not_found_code = "account.session_not_found"
    not_found_message = "会话不存在"
    id_detail_key = "session_id"
