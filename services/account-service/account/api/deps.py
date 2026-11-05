"""FastAPI 依赖:DB session、账号解析(§10)与配置。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from common.security import ACCOUNT_HEADER, require_internal_secret
from database.session import session_scope
from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from account.core.byok_kms import BYOKKms
from account.core.config import Settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_byok_kms(request: Request) -> BYOKKms:
    """BYOK KMS:lifespan 装配(测试可经 create_app 注入),业务层只依赖协议。"""
    return request.app.state.byok_kms


async def get_internal_account_id(
    _: Annotated[None, Depends(require_internal_secret)],
    x_account_id: Annotated[str | None, Header(alias=ACCOUNT_HEADER)] = None,
) -> str:
    """内部调用账号解析:共享密钥(common.security)+ 显式 X-Account-Id。

    服务间调用(orchestrator 上报用量、读配额快照)没有用户上下文,
    凭 X-Internal-Secret 认证;密钥不匹配/缺账号头都是 401。
    """
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
