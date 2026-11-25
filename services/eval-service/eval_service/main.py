"""eval-service 入口:service-kit 装配 + 数据库生命周期。"""

from __future__ import annotations

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from eval_service.api.health import health_router
from eval_service.api.v1.router import api_router
from eval_service.core.config import Settings
from eval_service.core.storage import build_store


def create_app(
    settings: Settings | None = None,
    token_verifier: JwtVerifier | None = None,
    artifact_store: ArtifactStore | None = None,
) -> FastAPI:
    """构造应用；测试注入 token_verifier / 报告存储。"""

    resolved = settings or Settings()

    async def startup(app: FastAPI) -> None:
        await install_database(app)
        store: ArtifactStore | None = app.state.artifact_store
        if store is None:
            store = build_store(resolved)
        app.state.artifact_store = store

    lifespan = build_lifespan(startup=startup)
    return create_service_app(
        name="eval-service",
        settings=resolved,
        lifespan=lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={
            "token_verifier": token_verifier,
            "artifact_store": artifact_store,
        },
    )


app = create_app()
