"""认证业务(自建,由 dev 桩转正):注册/登录/刷新/登出。

职责分工(§10):凭证校验(密码哈希)与签发指引归本模块,令牌签发归
core/idp.py 的 IdPSigner;组织/成员/会话归 services/account.py。

- 注册:email 全局唯一(当前约定,见 repositories.find_active_by_email),
  新建组织(租户)+ owner 成员;
- 登录:验证 argon2 密码;用户不存在也执行一次假哈希校验,防时序枚举;
- 刷新:旋转式——旧 refresh token 一次性,用后即作废并签新对;
  **已作废令牌被再次使用视为泄露信号,吊销该用户全部刷新令牌**;
- 登出:吊销指定 refresh token(access 不设黑名单,自然过期)。

refresh token 本体 = 256 位随机 urlsafe 字符串,库里只存 sha256;
仓储不提交,事务边界仍由 API 层 session_scope 负责。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from common.errors import ConflictError, UnauthorizedError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.config import Settings
from account.core.idp import IdPSigner
from account.models.account import RefreshToken, User
from account.repositories import RefreshTokenRepository, UserRepository
from account.schemas.account import SessionCreate
from account.schemas.auth import LoginRequest, RegisterRequest
from account.services.account import (
    create_session,
    require_or_create_organization,
)

_hasher = PasswordHasher()  # argon2id,默认参数(OWASP 推荐档位)
# 登录失败路径的"假哈希":用户不存在时也跑一次校验,消掉时序差
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

REFRESH_TOKEN_BYTES = 48  # 64 字符 urlsafe


@dataclass(frozen=True)
class AuthResult:
    """注册/登录/刷新的统一产出:双令牌 + 身份上下文。"""

    access_token: str
    refresh_token: str
    expires_in: int
    tenant_id: str
    organization_id: str
    user_id: str
    subject: str
    role: str
    session_id: str


def _hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    """sqlite 读回的 naive 时间补 UTC(sqlite 方言不保留时区信息)。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _verify_password(password: str, stored: str | None) -> bool:
    target = stored if stored is not None else _DUMMY_HASH
    try:
        return _hasher.verify(target, password) and stored is not None
    except VerificationError:
        # 含密码不匹配与哈希格式异常:统一按"不通过"处理
        return False


async def _issue_tokens(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    user: User,
    device: str,
) -> AuthResult:
    """签 access + 登记会话 + 生成并落库 refresh(旋转的另一半在 refresh())。"""
    record = await create_session(
        session, user.tenant_id, SessionCreate(user_id=user.id, device=device)
    )
    access = signer.sign(subject=user.subject, tenant_id=user.tenant_id)
    raw_refresh = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    await RefreshTokenRepository(session).add(
        RefreshToken(
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_hash=_hash_refresh(raw_refresh),
            expires_at=datetime.now(UTC)
            + timedelta(seconds=settings.refresh_ttl_seconds),
        )
    )
    return AuthResult(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=signer.ttl_seconds,
        tenant_id=user.tenant_id,
        organization_id=user.tenant_id,
        user_id=user.id,
        subject=user.subject,
        role=user.role,
        session_id=record.id,
    )


async def register(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    data: RegisterRequest,
) -> AuthResult:
    """注册:email 全局查重(409)→ 新组织 + owner 成员 → 签发。"""
    users = UserRepository(session)
    existing = await users.find_active_by_email(data.email)
    if existing is not None:
        raise ConflictError(
            "account.email_taken",
            "该邮箱已注册,请直接登录",
            details={"email": data.email},
        )

    tenant_id = secrets.token_hex(16)
    await require_or_create_organization(session, tenant_id, settings.default_plan)
    user = await users.add(
        User(
            tenant_id=tenant_id,
            subject=data.email,
            email=data.email,
            display_name=data.display_name,
            role="owner",
            status="active",
            password_hash=_hasher.hash(data.password),
        )
    )
    return await _issue_tokens(session, settings, signer, user, device="register")


async def login(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    data: LoginRequest,
) -> AuthResult:
    """登录:验密码;失败统一 401,不区分"没这人"与"密码错"(防枚举)。"""
    user = await UserRepository(session).find_active_by_email(data.email)
    if not _verify_password(data.password, user.password_hash if user else None):
        raise UnauthorizedError("auth.invalid_credentials", "邮箱或密码不正确")
    return await _issue_tokens(session, settings, signer, user, device="password-login")


async def refresh_tokens(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    raw_refresh: str,
) -> AuthResult:
    """旋转刷新:旧令牌一次性;重用已吊销令牌 → 吊销该用户全部(泄露防御)。"""
    repo = RefreshTokenRepository(session)
    record = await repo.find_by_hash(_hash_refresh(raw_refresh))
    if record is None:
        raise UnauthorizedError("auth.refresh_invalid", "刷新令牌无效")
    if record.revoked_at is not None:
        # 已作废的令牌又出现:视为泄露,切断该用户所有会话的续期通道。
        # 这是全模块唯一一次业务层显式提交:安全动作必须在抛 401 之前落盘,
        # 否则会随 session_scope 的"异常即回滚"一起被冲掉(之后的空提交无害)
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == record.user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
        raise UnauthorizedError("auth.refresh_reused", "刷新令牌已失效,请重新登录")

    now = datetime.now(UTC)
    if _aware(record.expires_at) <= now:
        raise UnauthorizedError("auth.refresh_expired", "刷新令牌已过期,请重新登录")

    user = await UserRepository(session).require_for_tenant(
        record.tenant_id, record.user_id
    )
    if user.status != "active":
        raise UnauthorizedError("auth.user_disabled", "成员已停用")

    record.revoked_at = now  # 旋转:旧令牌即刻作废
    return await _issue_tokens(session, settings, signer, user, device="refresh")


async def logout(session: AsyncSession, raw_refresh: str) -> None:
    """登出:吊销 refresh token;不存在的令牌静默成功(幂等)。"""
    repo = RefreshTokenRepository(session)
    record = await repo.find_by_hash(_hash_refresh(raw_refresh))
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await repo.add(record)
