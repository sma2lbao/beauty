"""认证业务(自建,由 dev 桩转正):注册/登录/刷新/登出/内部签发。

职责分工(§10):凭证校验(密码哈希)与签发流程归本模块,令牌签发归
core/idp.py 的 IdPSigner;账号档案/会话归 services/account.py。

- 注册:email 全局唯一,新建账号(account_id = users.id);
- 登录:验证 argon2 密码;账号不存在也执行一次假哈希校验,防时序枚举;
- 刷新:旋转式——旧 refresh token 一次性,用后即作废并签新对;
  **已作废令牌被再次使用视为泄露信号,吊销该账号全部刷新令牌**;
- 登出:吊销指定 refresh token(access 不设黑名单,自然过期);
- 内部签发:账号必须存在且 active,否则 401 auth.account_missing。

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
from account.core.oauth import OAuthProfile
from account.core.roles import ROLE_ADMIN, is_admin_email, role_for_email
from account.models.account import OAuthIdentity, RefreshToken, User
from account.repositories import (
    OAuthIdentityRepository,
    RefreshTokenRepository,
    UserRepository,
)
from account.schemas.auth import LoginRequest, RegisterRequest
from account.services.account import create_session

_hasher = PasswordHasher()  # argon2id,默认参数(OWASP 推荐档位)
# 登录失败路径的"假哈希":用户不存在时也跑一次校验,消掉时序差
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

REFRESH_TOKEN_BYTES = 48  # 64 字符 urlsafe


@dataclass(frozen=True)
class AuthResult:
    """注册/登录/刷新的统一产出:双令牌 + 账号身份。"""

    access_token: str
    refresh_token: str
    expires_in: int
    account_id: str
    subject: str
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


def _default_display_name(email: str) -> str:
    return email.split("@", 1)[0]


async def _sync_role(session: AsyncSession, settings: Settings, user: User) -> User:
    """登录时按 ADMIN_EMAILS 补持久化管理员角色(admin-plane §2.2)。

    只升不降:移出名单不会自动降权(降权走管理面显式改 role)。
    """
    if is_admin_email(settings, user.email) and user.role != ROLE_ADMIN:
        user.role = ROLE_ADMIN
        return await UserRepository(session).add(user)
    return user


async def _issue_tokens(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    user: User,
    device: str,
) -> AuthResult:
    """签 access + 登记会话 + 生成并落库 refresh(旋转的另一半在 refresh_tokens)。"""
    record = await create_session(session, user.id, device)
    access = signer.sign(subject=user.subject, account_id=user.id)
    raw_refresh = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    await RefreshTokenRepository(session).add(
        RefreshToken(
            account_id=user.id,
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
        account_id=user.id,
        subject=user.subject,
        session_id=record.id,
    )


async def register(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    data: RegisterRequest,
) -> AuthResult:
    """注册:email 全局查重(409)→ 新建账号 → 签发。"""
    users = UserRepository(session)
    existing = await users.find_by_email(data.email)
    if existing is not None:
        raise ConflictError(
            "account.email_taken",
            "该邮箱已注册,请直接登录",
            details={"email": data.email},
        )

    user = await users.add(
        User(
            subject=data.email,
            email=data.email,
            display_name=data.display_name or _default_display_name(data.email),
            status="active",
            role=role_for_email(settings, data.email),
            password_hash=_hasher.hash(data.password),
            quota={},
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
    user = await _sync_role(session, settings, user)
    return await _issue_tokens(session, settings, signer, user, device="password-login")


async def refresh_tokens(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    raw_refresh: str,
) -> AuthResult:
    """旋转刷新:旧令牌一次性;重用已吊销令牌 → 吊销该账号全部(泄露防御)。"""
    repo = RefreshTokenRepository(session)
    record = await repo.find_by_hash(_hash_refresh(raw_refresh))
    if record is None:
        raise UnauthorizedError("auth.refresh_invalid", "刷新令牌无效")
    if record.revoked_at is not None:
        # 已作废的令牌又出现:视为泄露,切断该账号所有会话的续期通道。
        # 这是全模块唯一一次业务层显式提交:安全动作必须在抛 401 之前落盘,
        # 否则会随 session_scope 的"异常即回滚"一起被冲掉(之后的空提交无害)
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.account_id == record.account_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await session.commit()
        raise UnauthorizedError("auth.refresh_reused", "刷新令牌已失效,请重新登录")

    now = datetime.now(UTC)
    if _aware(record.expires_at) <= now:
        raise UnauthorizedError("auth.refresh_expired", "刷新令牌已过期,请重新登录")

    user = await UserRepository(session).require(record.account_id)
    if user.status != "active":
        raise UnauthorizedError("auth.user_disabled", "账号已停用")

    record.revoked_at = now  # 旋转:旧令牌即刻作废
    return await _issue_tokens(session, settings, signer, user, device="refresh")


async def oauth_login(
    session: AsyncSession,
    settings: Settings,
    signer: IdPSigner,
    profile: OAuthProfile,
) -> AuthResult:
    """社交登录:已有身份直接签发;首次登录按 email 复用账号或新建。"""
    identities = OAuthIdentityRepository(session)
    identity = await identities.find_by_provider_subject(
        profile.provider, profile.subject
    )
    if identity is not None:
        user = await UserRepository(session).require(identity.account_id)
        if user.status != "active":
            raise UnauthorizedError("auth.user_disabled", "账号已停用")
        user = await _sync_role(session, settings, user)
        return await _issue_tokens(
            session, settings, signer, user, device=f"oauth-{profile.provider}"
        )

    users = UserRepository(session)
    user = await users.find_by_email(profile.email)
    if user is None:
        user = await users.add(
            User(
                subject=profile.email,
                email=profile.email,
                display_name=profile.name or _default_display_name(profile.email),
                status="active",
                role=role_for_email(settings, profile.email),
                password_hash=None,
                quota={},
            )
        )
    elif user.status != "active":
        raise UnauthorizedError("auth.user_disabled", "账号已停用")
    user = await _sync_role(session, settings, user)
    await identities.add(
        OAuthIdentity(
            account_id=user.id,
            provider=profile.provider,
            subject=profile.subject,
            user_id=user.id,
            email=profile.email,
        )
    )
    return await _issue_tokens(
        session, settings, signer, user, device=f"oauth-{profile.provider}"
    )


async def issue_token(
    session: AsyncSession,
    signer: IdPSigner,
    account_id: str,
    subject: str,
) -> str:
    """内部签发:账号不存在或非 active 一律 401 auth.account_missing。"""
    user = await UserRepository(session).get(account_id)
    if user is None or user.status != "active":
        raise UnauthorizedError("auth.account_missing", "账号不存在或未启用")
    return signer.sign(subject=subject, account_id=account_id)


async def logout(session: AsyncSession, raw_refresh: str) -> None:
    """登出:吊销 refresh token;不存在的令牌静默成功(幂等)。"""
    repo = RefreshTokenRepository(session)
    record = await repo.find_by_hash(_hash_refresh(raw_refresh))
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await repo.add(record)
