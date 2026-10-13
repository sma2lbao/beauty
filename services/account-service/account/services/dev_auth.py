"""dev 登录业务:幂等建档(组织/成员/会话)+ 签发 access token。

与 IdP 的分工(§10):凭证校验归 IdP,组织/成员/会话归本服务——
这个桩把两者串成一次"登录"调用:声明 email → 幂等建档 → 登记
会话 → 签发带 `tenant_id` claim 的 RS256 token。
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from account.core.config import Settings
from account.core.dev_idp import DevIdP
from account.models.account import User
from account.repositories import UserRepository
from account.schemas.account import SessionCreate
from account.schemas.auth import DevLoginRequest
from account.services.account import (
    create_session,
    require_or_create_organization,
)


@dataclass(frozen=True)
class DevLoginResult:
    access_token: str
    expires_in: int
    organization_id: str
    user_id: str
    subject: str
    role: str
    session_id: str


async def dev_login(
    session: AsyncSession,
    settings: Settings,
    idp: DevIdP,
    data: DevLoginRequest,
) -> DevLoginResult:
    """幂等登录:组织按 id 建档,成员按 (租户, email) 复用,会话每次新建。"""
    tenant_id = data.organization_id or uuid4().hex
    organization = await require_or_create_organization(
        session, tenant_id, settings.default_plan
    )
    if data.organization_name is not None:
        organization.name = data.organization_name

    users = UserRepository(session)
    existing = await users.list_scoped(tenant_id, User.email == data.email)
    if existing:
        user = existing[0]
        if data.display_name is not None:
            user.display_name = data.display_name
            user = await users.add(user)
    else:
        members = await users.list_scoped(tenant_id)
        user = User(
            tenant_id=tenant_id,
            subject=data.email,
            email=data.email,
            display_name=data.display_name,
            role="owner" if not members else "member",
            status="active",
        )
        user = await users.add(user)

    record = await create_session(
        session,
        tenant_id,
        SessionCreate(user_id=user.id, device="dev-login"),
    )
    token = idp.sign(subject=user.subject, tenant_id=tenant_id)
    return DevLoginResult(
        access_token=token,
        expires_in=idp.ttl_seconds,
        organization_id=organization.id,
        user_id=user.id,
        subject=user.subject,
        role=user.role,
        session_id=record.id,
    )
