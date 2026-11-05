"""服务入口:create_app 工厂 + lifespan(无 DB,纯模型出口)。"""

from __future__ import annotations

from common.service_kit import build_lifespan, create_service_app
from fastapi import FastAPI

from llm_gateway.api.health import health_router
from llm_gateway.api.v1.router import api_router
from llm_gateway.core.config import Settings
from llm_gateway.services.byok import ByokResolver
from llm_gateway.services.byok_catalog import ByokCatalog


async def _shutdown(app: FastAPI) -> None:
    """关闭 BYOK 密钥解析器与模型目录连接池(懒建,未用过则 no-op)。"""
    resolver: ByokResolver | None = getattr(app.state, "byok_resolver", None)
    if resolver is not None:
        await resolver.aclose()
    catalog: ByokCatalog | None = getattr(app.state, "byok_catalog", None)
    if catalog is not None:
        await catalog.aclose()


_lifespan = build_lifespan(shutdown=_shutdown)


def create_app(
    settings: Settings | None = None,
    *,
    byok_resolver: ByokResolver | None = None,
    byok_catalog: ByokCatalog | None = None,
) -> FastAPI:
    settings = settings or Settings()
    # BYOK 解析器/模型目录:测试可注入(带 MockTransport),默认按配置自建
    # (懒建连接池,未用到就不建)。
    return create_service_app(
        name="llm-gateway",
        settings=settings,
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={
            "byok_resolver": byok_resolver or ByokResolver(settings),
            "byok_catalog": byok_catalog or ByokCatalog(settings),
        },
    )


app = create_app()
