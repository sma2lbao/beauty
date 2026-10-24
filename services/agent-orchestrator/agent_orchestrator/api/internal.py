"""内部端点:只给 BFF 等兄弟服务调用,用 X-Internal-Secret 鉴权。

为什么单独一个 router:浏览器重定向到 BFF 的回调地址后,BFF 用服务间凭证
换 token(浏览器本身不持有 X-Internal-Secret);这也让回调不暴露在公网契约
/api/v1 里。
"""

from __future__ import annotations

from typing import Annotated

from common.errors import AppError
from common.security import make_internal_secret_dependency
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.mcp import (
    MCPOAuthCallbackRead,
    MCPOAuthCallbackRequest,
)
from agent_orchestrator.services import mcp_oauth

router = APIRouter(prefix="/internal", tags=["internal"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


_require_internal_secret = make_internal_secret_dependency(
    settings_attr="internal_auth_secret",
    error_code="internal.forbidden",
    error_message="内部调用凭证无效",
)


@router.post("/mcp/oauth/callback", response_model=MCPOAuthCallbackRead)
async def mcp_oauth_callback(
    body: MCPOAuthCallbackRequest,
    session: DBSession,
    settings: SettingsDep,
    _internal: Annotated[None, Depends(_require_internal_secret)],
) -> MCPOAuthCallbackRead:
    """BFF 转发浏览器回调:换 token 落库,返回前端应跳回的相对路径。

    业务失败也回 200(带 ok=false + redirect_path),让 BFF 统一 302 回前端提示,
    而不是把 4xx 甩给浏览器。
    """
    try:
        return await mcp_oauth.complete_authorization(
            session,
            state=body.state,
            code=body.code,
            settings=settings,
            secret=settings.mcp_secret,
        )
    except AppError as exc:
        return MCPOAuthCallbackRead(
            ok=False,
            redirect_path=mcp_oauth.redirect_path(ok=False, error=exc.message),
        )
