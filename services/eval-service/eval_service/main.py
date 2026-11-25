"""eval-service 入口:service-kit 装配 + 数据库生命周期。"""

from __future__ import annotations

from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from eval_service.api.health import health_router
from eval_service.api.v1.router import api_router
from eval_service.core.config import Settings


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """构造应用；测试注入 token_verifier。"""

    resolved = settings or Settings()

    async def startup(app: FastAPI) -> None:
        await install_database(app)

    lifespan = build_lifespan(startup=startup)
    return create_service_app(
        name="eval-service",
        settings=resolved,
        lifespan=lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={"token_verifier": token_verifier},
    )


app = create_app()
