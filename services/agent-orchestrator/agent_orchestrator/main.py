"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn agent_orchestrator.main:app --port 8002
Nx(仓库根):npx nx run agent-orchestrator:serve
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.errors import AppError
from common.logging import setup_logging
from database.base import Base
from database.session import create_session_factory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent_orchestrator.api.health import health_router
from agent_orchestrator.api.v1.router import api_router
from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.builder import build_run_graph
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM 元数据
from agent_orchestrator.services.llm import GatewayClient, LLMClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    setup_logging(settings)
    factory = create_session_factory(settings.database_url)
    app.state.session_factory = factory
    if settings.auto_create_tables:
        # dev/test 直建表;生产一律 alembic 迁移
        async with factory.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # LLM 装配(架构 §5 唯一出口):测试注入优先;否则 enabled 时起网关客户端
    llm: LLMClient | None = app.state.llm_client
    owns_llm = False
    if llm is None and settings.llm_enabled:
        llm = GatewayClient(
            base_url=settings.llm_gateway_url,
            timeout_seconds=settings.llm_timeout_seconds,
            model=settings.llm_model,
        )
        owns_llm = True

    try:
        if settings.database_url.startswith("postgres"):
            # postgres:图线程跨进程可恢复(架构 §6.4;setup 幂等建 checkpoint 表)。
            # 惰性导入:sqlite/dev 环境不需要 psycopg,模块加载零成本
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            pg_url = settings.database_url.replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
            async with AsyncPostgresSaver.from_conn_string(pg_url) as saver:
                await saver.setup()
                app.state.run_graph = build_run_graph(checkpointer=saver, llm=llm)
                yield
        else:
            # sqlite/dev:进程内 checkpointer
            app.state.run_graph = build_run_graph(llm=llm)
            yield
    finally:
        if owns_llm:
            await llm.aclose()
        await factory.engine.dispose()


def create_app(
    settings: Settings | None = None, llm_client: LLMClient | None = None
) -> FastAPI:
    """llm_client:测试注入 FakeLLM;生产留空,由 lifespan 按 llm_enabled 装配。"""
    app = FastAPI(title="agent-orchestrator", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.llm_client = llm_client

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
