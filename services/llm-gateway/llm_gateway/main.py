"""服务入口:create_app 工厂 + lifespan(无 DB,纯模型出口)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.logging import setup_logging
from common.middleware import RequestContextMiddleware, install_app_error_handler
from common.observability import install_observability
from fastapi import FastAPI

from llm_gateway.api.health import health_router
from llm_gateway.api.v1.router import api_router
from llm_gateway.core.config import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    # Sentry(P1)与 /internal/metrics(P2):DSN 为空/开关关闭时都是 no-op。
    install_observability(app, settings)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="llm-gateway", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=app.state.settings)

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
