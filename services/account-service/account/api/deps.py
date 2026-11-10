"""FastAPI 依赖:DB session、账号解析(§10)与配置。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import PermissionDeniedError, UnauthorizedError
from common.logging import bind_request_context, current_context
from common.security import ACCOUNT_HEADER, require_internal_secret
from database.session import session_scope
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.byok_kms import BYOKKms
from account.core.config import Settings
from account.core.roles import ROLE_ADMIN, is_admin_email
from account.models.account import User
from account.repositories import UserRepository
from account.services import audit


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_byok_kms(request: Request) -> BYOKKms:
    """BYOK KMS:lifespan 装配(测试可经 create_app 注入),业务层只依赖协议。"""
    return request.app.state.byok_kms


def remember_request_id(request: Request) -> None:
    """把 request_id 快照到 request.state(审计与日志都要用)。

    鉴权校验器首次验签会经 ASGI 传输发嵌套请求(读 JWKS),内层
    RequestContextMiddleware 结束时清空 contextvars,之后本请求从
    current_context() 读不到 request_id;scope 上的快照不受影响。
    """
    if getattr(request.state, "request_id", None) is None:
        request.state.request_id = current_context().get("request_id")


async def get_internal_account_id(
    request: Request,
    _: Annotated[None, Depends(require_internal_secret)],
    x_account_id: Annotated[str | None, Header(alias=ACCOUNT_HEADER)] = None,
) -> str:
    """内部调用账号解析:共享密钥(common.security)+ 显式 X-Account-Id。

    服务间调用(orchestrator 上报用量、读配额快照)没有用户上下文,
    凭 X-Internal-Secret 认证;密钥不匹配/缺账号头都是 401。
    """
    remember_request_id(request)
    if not x_account_id:
        raise UnauthorizedError("auth.account_missing", "内部调用缺少 X-Account-Id")
    bind_request_context(account_id=x_account_id)
    return x_account_id


async def get_account_id(
    request: Request,
    x_account_id: Annotated[str | None, Header(alias=ACCOUNT_HEADER)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """账号解析:IdP token 优先(默认);显式关闭鉴权才认临时头(与兄弟服务同约定)。"""
    remember_request_id(request)
    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_account_id:
            raise UnauthorizedError(
                "auth.account_missing",
                "缺少 X-Account-Id(鉴权未开启时的临时账号头)",
            )
        bind_request_context(account_id=x_account_id)
        return x_account_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(account_id=principal.account_id, user_id=principal.subject)
    return principal.account_id


async def require_admin(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    account_id: Annotated[str, Depends(get_account_id)],
) -> User:
    """管理面鉴权(admin-plane §3.1):角色只在库里,每请求判定。

    顺序:token 缺失/无效 → 401(由 get_account_id 负责);账号不存在 →
    403 admin.required;非 active → 403 admin.account_disabled;role=admin
    或邮箱命中 ADMIN_EMAILS → 放行;否则先落 denied 审计再 403。
    拒绝审计失败不改变 403 结论(仅告警)。
    """
    user = await UserRepository(session).get(account_id)
    if user is None:
        raise PermissionDeniedError("admin.required", "需要管理员权限")
    if user.status != "active":
        raise PermissionDeniedError("admin.account_disabled", "账号已停用")
    if user.role == ROLE_ADMIN or is_admin_email(get_settings(request), user.email):
        return user
    # 先取快照(rollback 会 expire 实例,之后再读属性会重新开读事务),
    # 再结束当前读事务,最后用独立短事务写审计:SQLite 下读事务未释放会锁库
    ctx = audit.context_from_request(request, user)
    await session.rollback()
    await audit.record_denied(
        request.app.state.session_factory,
        ctx,
        method=request.method,
        path=request.url.path,
    )
    raise PermissionDeniedError("admin.required", "需要管理员权限")
