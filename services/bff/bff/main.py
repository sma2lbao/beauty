"""BFF 服务入口:create_app 工厂 + lifespan。

M0-01 只承载健康检查;M0-05/06 在此装配 Cookie 会话与内部服务代理。

启动(项目目录内):uv run uvicorn bff.main:app --port 8007
Nx(仓库根):npx nx run bff:serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.errors import AppError
from common.logging import setup_logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from bff.api.health import health_router
from bff.core.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging(app.state.settings)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="bff", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    # 本地跨端口调试才需要;生产同源时该中间件不会命中。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app.state.settings.app_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    return app


app = create_app()
