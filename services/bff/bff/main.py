"""BFF 服务入口:create_app 工厂 + lifespan。

M0-05/06:同源 /api 门面——Cookie 会话、认证代理、内部服务代理、限流、
文件下载、stream token。启动(项目目录内):
uv run uvicorn bff.main:app --port 8007
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from common.logging import setup_logging
from common.middleware import (
    RequestContextMiddleware,
    inject_request_headers,
    install_app_error_handler,
)
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bff.api.auth import router as auth_router
from bff.api.health import health_router
from bff.api.mcp_oauth import router as mcp_oauth_router
from bff.api.proxy import router as proxy_router
from bff.core.config import Settings
from bff.core.rate_limit import SlidingWindowLimiter
from bff.core.security import SessionCodec


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    transport = app.state.transport
    app.state.upstream_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        transport=transport,
        follow_redirects=False,
        event_hooks={"request": [inject_request_headers]},
    )
    try:
        yield
    finally:
        await app.state.upstream_client.aclose()


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    app = FastAPI(title="bff", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.transport = transport
    app.state.session_codec = SessionCodec(app.state.settings.session_secret)
    app.state.rate_limiter = SlidingWindowLimiter(
        app.state.settings.rate_limit_per_minute
    )

    install_app_error_handler(app)

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app.state.settings.app_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 最后注册 => 最外层:限流 429 也要有 request_id 与访问日志
    app.add_middleware(RequestContextMiddleware, settings=app.state.settings)

    app.include_router(health_router)
    app.include_router(auth_router)
    # 必须在通用 /api/{path} 代理之前注册,否则回调会被代理吃掉
    app.include_router(mcp_oauth_router)
    app.include_router(proxy_router)
    return app


app = create_app()
