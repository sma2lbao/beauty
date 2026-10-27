"""服务入口:create_app 工厂 + lifespan(装配细节见 core/container.py)。

启动(项目目录内):uv run uvicorn agent_orchestrator.main:app --port 8002
Nx(仓库根):npx nx run agent-orchestrator:serve

本模块只做三件事:声明可注入依赖、组装 FastAPI(中间件/路由)、把生命周期
交给 AppContainer;所有外部客户端的装配与逆序关闭都在容器里。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.middleware import RequestContextMiddleware, install_app_error_handler
from common.tracing import exclude_telemetry
from events import outbox as outbox_models  # noqa: F401  注册 outbox/幂等表
from events.bus import EventBus
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent_orchestrator.api.health import health_router
from agent_orchestrator.api.internal import router as internal_router
from agent_orchestrator.api.mcp_server import router as mcp_server_router
from agent_orchestrator.api.v1.router import api_router
from agent_orchestrator.core.config import Settings
from agent_orchestrator.core.container import AppContainer, ToolkitFactory
from agent_orchestrator.graph import delta
from agent_orchestrator.models import approval as approval_models  # noqa: F401
from agent_orchestrator.models import (  # noqa: F401  注册 ORM 元数据(对话表)
    conversations as conversations_models,
)
from agent_orchestrator.models import mcp as mcp_models  # noqa: F401  注册 ORM 元数据
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM 元数据
from agent_orchestrator.models import skills as skills_models  # noqa: F401  注册 ORM
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.mcp_server import (
    ApiKeyVerifier,
    OAuthTokenIntrospector,
)
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.sandbox import Sandbox
from agent_orchestrator.services.skill_runner import SkillRunner
from agent_orchestrator.services.tool_client import ToolClient
from agent_orchestrator.services.tools import ToolExtrasFactory
from agent_orchestrator.services.workspace import Workspace


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动装配 / 关闭回收都委托给 AppContainer。"""
    container: AppContainer = app.state.container
    await container.start()
    try:
        yield
    finally:
        await container.stop()


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    toolkit_factory: ToolkitFactory | None = None,
    tool_client: ToolClient | None = None,
    tool_extras_factory: ToolExtrasFactory | None = None,
    token_verifier: JwtVerifier | None = None,
    artifact_store: ArtifactStore | None = None,
    workspace_client: Workspace | None = None,
    sandbox_client: Sandbox | None = None,
    skill_runner: SkillRunner | None = None,
    mcp_stdio_pool: StdioSandboxPool | None = None,
    event_bus: EventBus | None = None,
    api_key_verifier: ApiKeyVerifier | None = None,
    oauth_introspector: OAuthTokenIntrospector | None = None,
) -> FastAPI:
    """测试注入:llm_client、toolkit_factory、tool_extras_factory 等。"""
    app = FastAPI(
        title="agent-orchestrator",
        version="0.1.0",
        lifespan=lifespan,
        # 服务端 span 用 FastAPI 原生 telemetry;探针与指标端点不采样。
        telemetry={"exclude": exclude_telemetry},
    )
    # 容器同时把 settings 与注入项发布到 app.state(既有测试按名读取)
    container = AppContainer(
        app,
        settings or Settings(),
        llm_client=llm_client,
        toolkit_factory=toolkit_factory,
        tool_client=tool_client,
        tool_extras_factory=tool_extras_factory,
        token_verifier=token_verifier,
        artifact_store=artifact_store,
        workspace_client=workspace_client,
        sandbox_client=sandbox_client,
        skill_runner=skill_runner,
        mcp_stdio_pool=mcp_stdio_pool,
        event_bus=event_bus,
        api_key_verifier=api_key_verifier,
        oauth_introspector=oauth_introspector,
    )
    container.install()

    # 正文增量流是进程级通道配置(节点→services 的 sink),按启动时的 settings 固定
    delta.configure(
        enabled=container.settings.run_delta_streaming,
        flush_chars=container.settings.run_delta_flush_chars,
        flush_seconds=container.settings.run_delta_flush_seconds,
    )

    if container.settings.cors_allow_origins:
        # 浏览器直连(不经 Vite 代理)时需要;白名单来自配置,不用通配
        app.add_middleware(
            CORSMiddleware,
            allow_origins=container.settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    install_app_error_handler(app)
    app.add_middleware(RequestContextMiddleware, settings=container.settings)

    app.include_router(health_router)
    app.include_router(internal_router)
    app.include_router(mcp_server_router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
