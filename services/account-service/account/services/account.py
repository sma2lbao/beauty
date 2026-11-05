"""账号业务逻辑:账号档案与设备会话。

数据访问一律经仓储(`account.repositories`):账号过滤与 404 语义在仓储声明,
这里只写业务规则;仓储不提交,事务边界仍由 API 层的 session_scope 负责。
"""

from __future__ import annotations

from common.errors import ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from account.models.account import Session, User
from account.repositories import SessionRepository, UserRepository
from account.schemas.account import AccountUpdate


async def require_account(session: AsyncSession, account_id: str) -> User:
    """按账号 id 取账号;不存在即 404(不泄漏存在性)。"""
    return await UserRepository(session).require(account_id)


async def update_account(
    session: AsyncSession, account_id: str, data: AccountUpdate
) -> User:
    """改显示名;显式传 null 视为"清除",回落为 email 前缀(与注册默认一致)。"""
    users = UserRepository(session)
    account = await users.require(account_id)
    if "display_name" in data.model_fields_set:
        account.display_name = data.display_name or account.email.split("@", 1)[0]
    return await users.add(account)


async def create_session(
    session: AsyncSession, account_id: str, device: str
) -> Session:
    """登记一台设备会话:账号必须存在(404 早失败)。"""
    await require_account(session, account_id)
    record = Session(
        account_id=account_id, user_id=account_id, device=device, status="active"
    )
    return await SessionRepository(session).add(record)


async def list_sessions(session: AsyncSession, account_id: str) -> list[Session]:
    return await SessionRepository(session).list_scoped(
        account_id, order_by=[Session.created_at.desc(), Session.id.desc()]
    )


async def revoke_session(
    session: AsyncSession, account_id: str, session_id: str
) -> Session:
    sessions = SessionRepository(session)
    record = await sessions.require_for_account(account_id, session_id)
    if record.status != "active":
        raise ConflictError(
            "account.session_revoked",
            f"会话状态 {record.status} 不能重复吊销",
            details={"session_id": record.id, "status": record.status},
        )
    record.status = "revoked"
    return await sessions.add(record)
