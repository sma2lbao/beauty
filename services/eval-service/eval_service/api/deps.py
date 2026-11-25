"""FastAPI 依赖:DB session、账号解析与服务配置。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from common.auth import JwtVerifier
from common.errors import UnauthorizedError
from common.logging import bind_request_context
from database.session import session_scope
from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from eval_service.core.config import Settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


async def get_account_id(
    request: Request,
    x_account_id: Annotated[str | None, Header(alias="X-Account-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """账号解析:IdP token 优先,鉴权关闭时才认临时头。"""

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


async def get_viewer_account(
    request: Request,
    x_account_id: Annotated[str | None, Header(alias="X-Account-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """只读端点账号:鉴权关闭时允许匿名,开启时仍强制 Bearer。

    评测数据是平台全局数据、不是账号私有表;本地/演示环境按仓库惯例关闭
    AUTH_ENABLED,此时网关不会注入 X-Account-Id,只读端点不应因此 401
    (页面会静默回退到静态快照,看不到刚上报的运行)。写入口仍只认内部密钥,
    基线批准仍走 get_account_id 严格校验。
    """

    verifier: JwtVerifier | None = request.app.state.token_verifier
    if verifier is None:
        if not x_account_id:
            return None
        bind_request_context(account_id=x_account_id)
        return x_account_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("auth.token_missing", "缺少 Bearer token")
    principal = await verifier.verify(authorization.split(" ", 1)[1].strip())
    bind_request_context(account_id=principal.account_id, user_id=principal.subject)
    return principal.account_id


def get_settings(request: Request) -> Settings:
    return request.app.state.settings
