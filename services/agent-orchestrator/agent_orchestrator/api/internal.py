"""内部端点:只给 API 网关等兄弟服务调用,用 X-Internal-Secret 鉴权。

为什么单独一个 router:浏览器重定向到 API 网关的回调地址后,API 网关用服务间凭证
换 token(浏览器本身不持有 X-Internal-Secret);这也让回调不暴露在公网契约
/api/v1 里。
"""

from __future__ import annotations

from typing import Annotated, Any

from common.errors import AppError
from common.metrics import token_authorized
from common.security import make_internal_secret_dependency
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_session, get_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.schemas.mcp import (
    MCPOAuthCallbackRead,
    MCPOAuthCallbackRequest,
)
from agent_orchestrator.services import alerts as alerts_service
from agent_orchestrator.services import mcp_oauth

router = APIRouter(prefix="/internal", tags=["internal"])

#: 运维告警读取面(见 docs/design/alerting.md §4);不进 /api/v1 公共契约。
ALERTS_PATH = "/internal/alerts"

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
    """API 网关转发浏览器回调:换 token 落库,返回前端应跳回的相对路径。

    业务失败也回 200(带 ok=false + redirect_path),让 API 网关统一 302 回前端提示,
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
            redirect_path=mcp_oauth.redirect_path(
                ok=False,
                error=exc.message,
                # state 能验签就回用户发起授权的页面(预设连接器页),否则降级 /mcp
                next_path=mcp_oauth.next_path_from_state(settings, body.state),
            ),
        )


def install_alerts_endpoint(app: Any, settings: Settings) -> bool:
    """挂载 `GET /internal/alerts`;告警关闭时不注册(404,不暴露端点存在性)。

    令牌口径与 `/internal/metrics` 完全一致(共用 `token_authorized`):运维面
    不该有两套鉴权。读取的是 alerts 表的当前视图,firing 是待办、resolved 是回看。
    """
    if not settings.alerts_enabled:
        return False
    token = settings.metrics_token

    @app.get(ALERTS_PATH, include_in_schema=False)
    async def alerts_endpoint(
        request: Request,
        session: DBSession,
        _settings: SettingsDep,
    ) -> JSONResponse:
        if token and not token_authorized(request, token):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return JSONResponse(await alerts_service.snapshot(session))

    return True
