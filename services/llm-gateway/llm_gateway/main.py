"""服务入口:create_app 工厂 + lifespan(无 DB,纯模型出口)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.logging import setup_logging
from common.middleware import RequestContextMiddleware, install_app_error_handler
from common.observability import install_observability
from common.tracing import exclude_telemetry
from fastapi import FastAPI

from llm_gateway.api.health import health_router
from llm_gateway.api.v1.router import api_router
from llm_gateway.core.config import Settings
from llm_gateway.services.byok import ByokResolver


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    # Sentry(P1)与 /internal/metrics(P2):DSN 为空/开关关闭时都是 no-op。
    install_observability(app, settings)
    try:
        yield
    finally:
        # 关闭 BYOK 连接池(懒建,未解析过则 no-op)。
        resolver: ByokResolver | None = getattr(app.state, "byok_resolver", None)
        if resolver is not None:
            await resolver.aclose()


def create_app(
    settings: Settings | None = None,
    *,
    byok_resolver: ByokResolver | None = None,
) -> FastAPI:
    app = FastAPI(
        title="llm-gateway",
        version="0.1.0",
        lifespan=lifespan,
        # 服务端 span 用 FastAPI 原生 telemetry;探针与指标端点不采样。
        telemetry={"exclude": exclude_telemetry},
    )
    app.state.settings = settings or Settings()
    # BYOK 解析器:测试可注入(带 MockTransport),默认按配置自建(懒建连接池)。
    app.state.byok_resolver = byok_resolver or ByokResolver(app.state.settings)

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=app.state.settings)

    app.include_router(health_router)
    app.include_router(api_router, prefix="/v1")
    return app


app = create_app()
