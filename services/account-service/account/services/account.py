"""账号业务逻辑:组织(幂等建档)、成员、会话。"""

from __future__ import annotations

from common.errors import ConflictError, NotFoundError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from account.models.account import Organization, Session, User
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
    organization = await session.get(Organization, tenant_id)
    if organization is None:
        organization = Organization(
            id=tenant_id,
            name=f"组织 {tenant_id[:8]}",
            plan=default_plan,
            quota={},
        )
        session.add(organization)
        await session.flush()
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
    await session.flush()
    return organization


async def create_user(session: AsyncSession, tenant_id: str, data: UserCreate) -> User:
    """登记成员:subject 同租户唯一(重复登记是 409,不是覆盖)。"""
    duplicate = await session.scalar(
        select(User).where(User.tenant_id == tenant_id, User.subject == data.subject)
    )
    if duplicate is not None:
        raise ConflictError(
            "account.user_exists",
            "该成员已存在",
            details={"user_id": duplicate.id, "subject": duplicate.subject},
        )
    user = User(
        tenant_id=tenant_id,
        subject=data.subject,
        email=data.email,
        display_name=data.display_name,
        role=data.role,
        status="active",
    )
    session.add(user)
    await session.flush()
    return user


async def list_users(session: AsyncSession, tenant_id: str) -> list[User]:
    rows = await session.scalars(
        select(User)
        .where(User.tenant_id == tenant_id)
        .order_by(User.created_at.asc(), User.id.asc())
    )
    return list(rows)


async def require_user(session: AsyncSession, tenant_id: str, user_id: str) -> User:
    user = await session.scalar(
        select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    )
    if user is None:
        raise NotFoundError(
            "account.user_not_found", "成员不存在", details={"user_id": user_id}
        )
    return user


async def update_user(
    session: AsyncSession, tenant_id: str, user_id: str, data: UserUpdate
) -> User:
    user = await require_user(session, tenant_id, user_id)
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.role is not None:
        user.role = data.role
    if data.status is not None:
        user.status = data.status
    await session.flush()
    return user


async def create_session(
    session: AsyncSession, tenant_id: str, data: SessionCreate
) -> Session:
    """登记一台设备会话:成员必须存在(404 早失败)。"""
    await require_user(session, tenant_id, data.user_id)
    record = Session(
        tenant_id=tenant_id,
        user_id=data.user_id,
        device=data.device,
        status="active",
    )
    session.add(record)
    await session.flush()
    return record


async def list_sessions(session: AsyncSession, tenant_id: str) -> list[Session]:
    rows = await session.scalars(
        select(Session)
        .where(Session.tenant_id == tenant_id)
        .order_by(Session.created_at.desc(), Session.id.desc())
    )
    return list(rows)


async def revoke_session(
    session: AsyncSession, tenant_id: str, session_id: str
) -> Session:
    record = await session.scalar(
        select(Session).where(Session.id == session_id, Session.tenant_id == tenant_id)
    )
    if record is None:
        raise NotFoundError(
            "account.session_not_found",
            "会话不存在",
            details={"session_id": session_id},
        )
    if record.status != "active":
        raise ConflictError(
            "account.session_revoked",
            f"会话状态 {record.status} 不能重复吊销",
            details={"session_id": record.id, "status": record.status},
        )
    record.status = "revoked"
    await session.flush()
    return record
