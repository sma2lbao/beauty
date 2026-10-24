"""服务入口:service-kit 统一装配(create_service_app + build_lifespan)。

启动(项目目录内):uv run uvicorn knowledge.main:app --port 8006
Nx(仓库根):npx nx run knowledge-service:serve
"""

from __future__ import annotations

from common.auth import JwtVerifier
from common.service_kit import build_lifespan, create_service_app, install_database
from fastapi import FastAPI

from knowledge.api.health import health_router
from knowledge.api.v1.router import api_router
from knowledge.core.config import Settings
from knowledge.models import (
    knowledge as knowledge_models,  # noqa: F401  注册 ORM 元数据
)
from knowledge.services.embeddings import EmbeddingProvider, build_embedder


async def _startup(app: FastAPI) -> None:
    """先建 DB,再装配嵌入提供方(测试注入优先;配置错误在此快速失败)。"""
    await install_database(app)
    settings: Settings = app.state.settings
    app.state.embedder = app.state.embedder or build_embedder(
        settings.embedding_provider,
        model=settings.embedding_model,
        gateway_url=settings.llm_gateway_url,
        timeout_seconds=settings.embedding_timeout_seconds,
    )


async def _shutdown(app: FastAPI) -> None:
    """嵌入提供方可能持有 httpx client:先于 engine 释放关闭。"""
    closer = getattr(app.state.embedder, "aclose", None)
    if closer is not None:
        await closer()


_lifespan = build_lifespan(startup=_startup, shutdown=_shutdown)


def create_app(
    settings: Settings | None = None,
    embedder: EmbeddingProvider | None = None,
    token_verifier: JwtVerifier | None = None,
) -> FastAPI:
    """测试注入:embedder(假嵌入/本地提供方)、token_verifier(假 JWKS)。"""
    return create_service_app(
        name="knowledge-service",
        settings=settings or Settings(),
        lifespan=_lifespan,
        routers=[(health_router, ""), (api_router, "/v1")],
        state={"embedder": embedder, "token_verifier": token_verifier},
    )


app = create_app()
