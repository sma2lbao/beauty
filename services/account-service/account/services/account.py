"""账号业务逻辑:组织(幂等建档)、成员、会话。

数据访问一律经仓储(`account.repositories`):租户过滤与 404 语义在仓储声明,
这里只写业务规则;仓储不提交,事务边界仍由 API 层的 session_scope 负责。
"""

from __future__ import annotations

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from account.models.account import Organization, Session, User
from account.repositories import (
    OrganizationRepository,
    SessionRepository,
    UserRepository,
)
from account.schemas.account import (
    OrganizationUpdate,
    SessionCreate,
    UserCreate,
    UserUpdate,
)


async def require_or_create_organization(
    session: AsyncSession, tenant_id: str, default_plan: str
) -> Organization:
    """取当前租户的组织档案;没有就建(幂等,便于 dev/test 直接调用)。"""
    organizations = OrganizationRepository(session)
    organization = await organizations.get(tenant_id)
    if organization is None:
        organization = Organization(
            id=tenant_id,
            name=f"组织 {tenant_id[:8]}",
            plan=default_plan,
            quota={},
        )
        await organizations.add(organization)
    return organization


async def update_organization(
    session: AsyncSession,
    tenant_id: str,
    data: OrganizationUpdate,
    default_plan: str,
) -> Organization:
    organization = await require_or_create_organization(
        session, tenant_id, default_plan
    )
    if data.name is not None:
        organization.name = data.name
    if data.plan is not None:
        organization.plan = data.plan
    if data.quota is not None:
        organization.quota = data.quota
    await OrganizationRepository(session).add(organization)
    return organization


async def create_user(session: AsyncSession, tenant_id: str, data: UserCreate) -> User:
    """登记成员:subject 同租户唯一(重复登记是 409,不是覆盖)。"""
    users = UserRepository(session)
    duplicate = await users.list_scoped(tenant_id, User.subject == data.subject)
    if duplicate:
        existing = duplicate[0]
        raise ConflictError(
            "account.user_exists",
            "该成员已存在",
            details={"user_id": existing.id, "subject": existing.subject},
        )
    user = User(
        tenant_id=tenant_id,
        subject=data.subject,
        email=data.email,
        display_name=data.display_name,
        role=data.role,
        status="active",
    )
    return await users.add(user)


async def list_users(session: AsyncSession, tenant_id: str) -> list[User]:
    return await UserRepository(session).list_scoped(
        tenant_id, order_by=[User.created_at, User.id]
    )


async def require_user(session: AsyncSession, tenant_id: str, user_id: str) -> User:
    return await UserRepository(session).require_for_tenant(tenant_id, user_id)


async def update_user(
    session: AsyncSession, tenant_id: str, user_id: str, data: UserUpdate
) -> User:
    users = UserRepository(session)
    user = await users.require_for_tenant(tenant_id, user_id)
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        user.role = data.role
    if data.status is not None:
        user.status = data.status
    return await users.add(user)


async def create_session(
    session: AsyncSession, tenant_id: str, data: SessionCreate
) -> Session:
    """登记一台设备会话:成员必须存在(404 早失败)。"""
    await UserRepository(session).require_for_tenant(tenant_id, data.user_id)
    record = Session(
        tenant_id=tenant_id,
        user_id=data.user_id,
        device=data.device,
        status="active",
    )
    return await SessionRepository(session).add(record)


async def list_sessions(session: AsyncSession, tenant_id: str) -> list[Session]:
    return await SessionRepository(session).list_scoped(
        tenant_id, order_by=[Session.created_at.desc(), Session.id.desc()]
    )


async def revoke_session(
    session: AsyncSession, tenant_id: str, session_id: str
) -> Session:
    sessions = SessionRepository(session)
    record = await sessions.require_for_tenant(tenant_id, session_id)
    if record.status != "active":
        raise ConflictError(
            "account.session_revoked",
            f"会话状态 {record.status} 不能重复吊销",
            details={"session_id": record.id, "status": record.status},
        )
    record.status = "revoked"
    return await sessions.add(record)
