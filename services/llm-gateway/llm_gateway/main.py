"""服务入口:create_app 工厂 + lifespan(无 DB,纯模型出口)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.errors import AppError
from common.logging import setup_logging
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from llm_gateway.api.health import health_router
from llm_gateway.api.v1.router import api_router
from llm_gateway.core.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="llm-gateway", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
