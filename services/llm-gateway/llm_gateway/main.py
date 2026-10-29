"""服务入口:create_app 工厂 + lifespan(无 DB,纯模型出口)。"""

from __future__ import annotations

from common.service_kit import build_lifespan, create_service_app
from fastapi import FastAPI

from llm_gateway.api.health import health_router
from llm_gateway.api.v1.router import api_router
from llm_gateway.core.config import Settings
from llm_gateway.services.byok import ByokResolver


async def _shutdown(app: FastAPI) -> None:
    """关闭 BYOK 连接池(懒建,未解析过则 no-op)。"""
    resolver: ByokResolver | None = getattr(app.state, "byok_resolver", None)
    if resolver is not None:
        await resolver.aclose()


_lifespan = build_lifespan(shutdown=_shutdown)


def create_app(
    settings: Settings | None = None,
    *,
    byok_resolver: ByokResolver | None = None,
) -> FastAPI:
    settings = settings or Settings()
    # BYOK 解析器:测试可注入(带 MockTransport),默认按配置自建(懒建连接池)。
    return create_service_app(
        name="llm-gateway",
        settings=settings,
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={"byok_resolver": byok_resolver or ByokResolver(settings)},
    )


app = create_app()
