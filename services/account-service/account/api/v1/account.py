"""账号端点:组织档案/配额、成员、会话。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from account.api.deps import get_session, get_settings, get_tenant_id
from account.core.config import Settings
from account.schemas.account import (
    OrganizationRead,
    OrganizationUpdate,
    SessionCreate,
    SessionRead,
    UserCreate,
    UserRead,
    UserUpdate,
)
from account.services import account as account_service

router = APIRouter(tags=["account"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/organization", response_model=OrganizationRead)
async def get_organization(
    session: DBSession, tenant_id: TenantId, settings: SettingsDep
) -> OrganizationRead:
    """当前租户的组织档案与配额(首次读取幂等建档)。"""
    organization = await account_service.require_or_create_organization(
        session, tenant_id, settings.default_plan
    )
    return OrganizationRead.model_validate(organization)


@router.patch("/organization", response_model=OrganizationRead)
async def update_organization(
    body: OrganizationUpdate,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
) -> OrganizationRead:
    """更新组织名称/套餐/配额(审计留痕在平台侧,阶段 B 补事件)。"""
    organization = await account_service.update_organization(
        session, tenant_id, body, settings.default_plan
    )
    return OrganizationRead.model_validate(organization)


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate, session: DBSession, tenant_id: TenantId
) -> UserRead:
    """登记成员(subject 为 IdP sub,同租户唯一)。"""
    user = await account_service.create_user(session, tenant_id, body)
    return UserRead.model_validate(user)


@router.get("/users", response_model=list[UserRead])
async def list_users(session: DBSession, tenant_id: TenantId) -> list[UserRead]:
    users = await account_service.list_users(session, tenant_id)
    return [UserRead.model_validate(user) for user in users]


@router.get("/users/{user_id}", response_model=UserRead)
async def get_user(user_id: str, session: DBSession, tenant_id: TenantId) -> UserRead:
    user = await account_service.require_user(session, tenant_id, user_id)
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead)
async def update_user(
    body: UserUpdate, user_id: str, session: DBSession, tenant_id: TenantId
) -> UserRead:
    """改昵称/角色/状态(停用即 status=disabled,不物理删除)。"""
    user = await account_service.update_user(session, tenant_id, user_id, body)
    return UserRead.model_validate(user)


@router.post(
    "/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED
)
async def create_session(
    body: SessionCreate, session: DBSession, tenant_id: TenantId
) -> SessionRead:
    """登记设备会话(app 预留,§7)。"""
    record = await account_service.create_session(session, tenant_id, body)
    return SessionRead.model_validate(record)


@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(session: DBSession, tenant_id: TenantId) -> list[SessionRead]:
    records = await account_service.list_sessions(session, tenant_id)
    return [SessionRead.model_validate(record) for record in records]


@router.post("/sessions/{session_id}/revoke", response_model=SessionRead)
async def revoke_session(
    session_id: str, session: DBSession, tenant_id: TenantId
) -> SessionRead:
    """吊销会话(重复吊销 409)。"""
    record = await account_service.revoke_session(session, tenant_id, session_id)
    return SessionRead.model_validate(record)
