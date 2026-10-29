"""BFF 服务入口:create_app 工厂 + lifespan(装配统一走 common.service_kit)。

M0-05/06:同源 /api 门面——Cookie 会话、认证代理、内部服务代理、限流、
文件下载、stream token。启动(项目目录内):
uv run uvicorn bff.main:app --port 8007
"""

from __future__ import annotations

import httpx
from common.middleware import inject_request_headers
from common.security import SlidingWindowLimiter
from common.service_kit import build_lifespan, create_service_app
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from bff.api.auth import router as auth_router
from bff.api.health import health_router
from bff.api.mcp_oauth import router as mcp_oauth_router
from bff.api.proxy import router as proxy_router
from bff.core.config import Settings
from bff.core.security import SessionCodec


async def _startup(app: FastAPI) -> None:
    """启动:建上游 HTTP 客户端(测试可注入 transport,不打真实网络)。"""
    app.state.upstream_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        transport=app.state.transport,
        follow_redirects=False,
        event_hooks={"request": [inject_request_headers]},
    )


async def _shutdown(app: FastAPI) -> None:
    """关闭:回收上游 HTTP 客户端连接池(启动失败时客户端可能未建)。"""
    client: httpx.AsyncClient | None = getattr(app.state, "upstream_client", None)
    if client is not None:
        await client.aclose()


_lifespan = build_lifespan(startup=_startup, shutdown=_shutdown)


async def _rate_limit(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """限流中间件:仅作用于 /api/(同源门面),已登录按租户、未登录按客户端 IP。"""
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    app = request.app
    state = app.state.session_codec.decode(
        request.cookies.get(app.state.settings.cookie_name)
    )
    tenant = (state or {}).get("user", {}).get("tenant_id")
    client_host = request.client.host if request.client else "unknown"
    group = "/".join(path.split("/")[:4])
    if not await app.state.rate_limiter.allow(f"{tenant or client_host}:{group}"):
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "60"},
            content={
                "code": "bff.rate_limited",
                "message": "请求过于频繁,请稍后再试",
                "details": {},
            },
        )
    return await call_next(request)


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings()
    return create_service_app(
        name="bff",
        settings=settings,
        lifespan=_lifespan,
        routers=[
            (health_router, ""),
            (auth_router, ""),
            # 必须在通用 /api/{path} 代理之前注册,否则回调会被代理吃掉
            (mcp_oauth_router, ""),
            (proxy_router, ""),
        ],
        state={
            "transport": transport,
            "session_codec": SessionCodec(settings.session_secret),
            "rate_limiter": SlidingWindowLimiter(settings.rate_limit_per_minute),
        },
        cors_origins=[settings.app_origin],
        # 限流在 CORS 之内、统一错误处理之后;请求上下文仍是最外层。
        middlewares=[(BaseHTTPMiddleware, {"dispatch": _rate_limit})],
    )


app = create_app()
