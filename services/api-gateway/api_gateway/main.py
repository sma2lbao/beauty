"""API 网关服务入口:create_app 工厂 + lifespan(装配统一走 common.service_kit)。

M0-05/06:同源 /api 门面——Cookie 会话、认证代理、内部服务代理、限流、
文件下载、stream token。启动(项目目录内):
uv run uvicorn api_gateway.main:app --port 8007
"""

from __future__ import annotations

from typing import Any

import httpx
from common.logging import get_logger
from common.middleware import inject_request_headers
from common.security import build_rate_limiter
from common.service_kit import build_lifespan, create_service_app
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from api_gateway.api.auth import router as auth_router
from api_gateway.api.connector_oauth import router as connector_oauth_router
from api_gateway.api.health import health_router
from api_gateway.api.mcp_oauth import router as mcp_oauth_router
from api_gateway.api.proxy import router as proxy_router
from api_gateway.core.config import Settings
from api_gateway.core.security import SessionCodec

logger = get_logger(__name__)


async def _startup(app: FastAPI) -> None:
    """启动:建上游 HTTP 客户端(测试可注入 transport,不打真实网络)。"""
    app.state.upstream_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        transport=app.state.transport,
        follow_redirects=False,
        event_hooks={"request": [inject_request_headers]},
    )


async def _shutdown(app: FastAPI) -> None:
    """关闭:回收上游 HTTP 客户端连接池与限流后端(启动失败时可能未建)。"""
    client: httpx.AsyncClient | None = getattr(app.state, "upstream_client", None)
    if client is not None:
        await client.aclose()
    limiter = getattr(app.state, "rate_limiter", None)
    if limiter is not None:
        try:
            await limiter.aclose()
        except Exception:  # noqa: BLE001 - 回收失败不应阻止进程退出
            logger.warning("rate_limit.close_failed", exc_info=True)


_lifespan = build_lifespan(startup=_startup, shutdown=_shutdown)


async def _rate_limit(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """限流中间件:仅作用于 /api/(同源门面),已登录按账号、未登录按客户端 IP。"""
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    app = request.app
    state = app.state.session_codec.decode(
        request.cookies.get(app.state.settings.cookie_name)
    )
    account = (state or {}).get("user", {}).get("account_id")
    client_host = request.client.host if request.client else "unknown"
    group = "/".join(path.split("/")[:4])
    if not await app.state.rate_limiter.allow(f"{account or client_host}:{group}"):
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "60"},
            content={
                "code": "api_gateway.rate_limited",
                "message": "请求过于频繁,请稍后再试",
                "details": {},
            },
        )
    return await call_next(request)


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    rate_limiter: Any = None,
) -> FastAPI:
    """测试注入:transport(假上游 HTTP)、rate_limiter(假限流器)。"""
    settings = settings or Settings()
    return create_service_app(
        name="api-gateway",
        settings=settings,
        lifespan=_lifespan,
        routers=[
            (health_router, ""),
            (auth_router, ""),
            # 以下是浏览器直连的公开回调(第三方授权页重定向回来,不带会话),
            # 必须在通用 /api/{path} 代理之前注册,否则会被代理 401 掉
            (mcp_oauth_router, ""),
            (connector_oauth_router, ""),
            (proxy_router, ""),
        ],
        state={
            "transport": transport,
            "session_codec": SessionCodec(settings.session_secret),
            "rate_limiter": rate_limiter
            or build_rate_limiter(
                limit=settings.rate_limit_per_minute,
                redis_url=settings.redis_url,
                namespace="api-gateway",
            ),
        },
        cors_origins=[settings.app_origin],
        # 限流在 CORS 之内、统一错误处理之后;请求上下文仍是最外层。
        middlewares=[(BaseHTTPMiddleware, {"dispatch": _rate_limit})],
    )


app = create_app()
