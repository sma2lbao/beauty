"""服务入口:create_app 工厂 + lifespan。

启动(项目目录内):uv run uvicorn agent_orchestrator.main:app --port 8002
Nx(仓库根):npx nx run agent-orchestrator:serve
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from functools import partial

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.errors import AppError
from common.logging import setup_logging
from database.base import Base
from database.session import create_session_factory
from events import outbox as outbox_models  # noqa: F401  注册 outbox/幂等表
from events.bus import EventBus, InMemoryEventBus
from events.outbox import OutboxDispatcher
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from agent_orchestrator.api.health import health_router
from agent_orchestrator.api.v1.router import api_router
from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.builder import build_run_graph
from agent_orchestrator.models import (  # noqa: F401  注册 ORM 元数据(对话表)
    conversations as conversations_models,
)
from agent_orchestrator.models import runs as runs_models  # noqa: F401  注册 ORM 元数据
from agent_orchestrator.services.artifacts import build_store
from agent_orchestrator.services.calendar import CalendarFeed
from agent_orchestrator.services.connectors import ConnectorClient
from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.knowledge import KnowledgeClient
from agent_orchestrator.services.llm import GatewayClient, LLMClient
from agent_orchestrator.services.outbox import (
    record_run_event,
    register_consumers,
)
from agent_orchestrator.services.token_broker import TokenBroker
from agent_orchestrator.services.tools import ToolRegistry, build_tools
from agent_orchestrator.services.workspace import Workspace, WorkspaceClient

ToolkitFactory = Callable[[str], ToolRegistry]


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

    # 工具装配(架构 §8):测试注入 toolkit_factory 优先;
    # 否则 tools_enabled 时按子开关装配各能力提供方(未开启的不暴露)
    toolkit_factory: ToolkitFactory | None = app.state.toolkit_factory
    connector: ConnectorClient | None = None
    knowledge: KnowledgeClient | None = None
    # 服务间 token(§5):内部调用一律经 TokenBroker 换标准 JWT——租户来自
    # 签名 claim,不再有 X-Tenant-Id 裸调路径;密钥未配置时在换发处明确报错
    broker = TokenBroker(
        base_url=settings.auth_issuer,  # 签发方即 account-service
        internal_secret=settings.internal_auth_secret,
    )
    token_provider = broker.token
    if toolkit_factory is None and settings.tools_enabled:
        connector = ConnectorClient(
            settings.connector_service_url,
            settings.tool_timeout_seconds,
            token_provider=token_provider,
        )
        documents = (
            DocumentLibrary(settings.documents_root, settings.documents_max_bytes)
            if settings.documents_enabled
            else None
        )
        calendar = (
            CalendarFeed(settings.calendar_ics_path)
            if settings.calendar_enabled
            else None
        )
        knowledge = (
            KnowledgeClient(
                settings.knowledge_service_url,
                settings.knowledge_timeout_seconds,
                token_provider=token_provider,
            )
            if settings.knowledge_enabled
            else None
        )
        toolkit_factory = partial(
            build_tools,
            connector=connector,
            documents=documents,
            calendar=calendar,
            knowledge=knowledge,
        )

    # 鉴权装配(架构 §10):测试注入优先;否则 auth_enabled 时起 token 校验器
    verifier: JwtVerifier | None = app.state.token_verifier
    owns_verifier = False
    if verifier is None and settings.auth_enabled:
        verifier = JwtVerifier(
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            jwks_url=settings.auth_jwks_url,
            tenant_claim=settings.auth_tenant_claim,
        )
        owns_verifier = True
    app.state.token_verifier = verifier

    # 交付物镜像到工作区(§7):测试注入优先;独立于工具箱开关
    workspace: WorkspaceClient | None = app.state.workspace_client
    if workspace is None and settings.workspace_enabled:
        workspace = WorkspaceClient(
            settings.workspace_service_url,
            settings.workspace_timeout_seconds,
            token_provider=token_provider,
        )
    app.state.workspace_client = workspace

    # 交付物存储装配(§7 阶段 B):测试注入优先;否则按配置选后端
    # (database 模式为 None——内容留在 run_artifacts.content 列)
    store: ArtifactStore | None = app.state.artifact_store
    if store is None:
        store = build_store(settings)
    app.state.artifact_store = store

    # 事务性 outbox(§6.3/§8):测试注入优先 —— recorder 挂在 session.info,
    # dispatcher 后台投递;首个消费者是"交付物自动入知识库"
    bus: EventBus = app.state.event_bus or InMemoryEventBus()
    app.state.event_bus = bus
    recorder = None
    dispatcher: OutboxDispatcher | None = None
    outbox_stop: asyncio.Event | None = None
    outbox_task: asyncio.Task[None] | None = None
    ingest_knowledge: KnowledgeClient | None = None
    if settings.outbox_enabled:
        recorder = record_run_event
        if settings.knowledge_ingest_enabled:
            ingest_knowledge = KnowledgeClient(
                settings.knowledge_service_url,
                settings.knowledge_timeout_seconds,
                token_provider=token_provider,
            )
            consumers = register_consumers(
                bus,
                knowledge=ingest_knowledge,
                session_factory=factory,
                store=store,
            )
            app.state.outbox_consumers = consumers
        dispatcher = OutboxDispatcher(
            factory,
            bus,
            batch_size=settings.outbox_batch_size,
            max_attempts=settings.outbox_max_attempts,
        )
        outbox_stop = asyncio.Event()
        outbox_task = asyncio.create_task(
            dispatcher.run_forever(
                interval_seconds=settings.outbox_dispatch_interval_seconds,
                stop=outbox_stop,
            )
        )
    app.state.outbox_recorder = recorder
    app.state.outbox_dispatcher = dispatcher

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
                app.state.run_graph = build_run_graph(
                    checkpointer=saver, llm=llm, toolkit_factory=toolkit_factory
                )
                yield
        else:
            # sqlite/dev:进程内 checkpointer
            app.state.run_graph = build_run_graph(
                llm=llm, toolkit_factory=toolkit_factory
            )
            yield
    finally:
        if outbox_stop is not None:
            outbox_stop.set()
        if outbox_task is not None:
            outbox_task.cancel()
            with suppress(asyncio.CancelledError):
                await outbox_task
        if ingest_knowledge is not None:
            await ingest_knowledge.aclose()
        if owns_llm:
            await llm.aclose()
        if owns_verifier:
            await verifier.aclose()
        if connector is not None:
            await connector.aclose()
        if knowledge is not None:
            await knowledge.aclose()
        await broker.aclose()
        await factory.engine.dispose()


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    toolkit_factory: ToolkitFactory | None = None,
    token_verifier: JwtVerifier | None = None,
    artifact_store: ArtifactStore | None = None,
    workspace_client: Workspace | None = None,
    event_bus: EventBus | None = None,
) -> FastAPI:
    """测试注入:llm_client、toolkit_factory、token_verifier、artifact_store。"""
    app = FastAPI(title="agent-orchestrator", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.llm_client = llm_client
    app.state.toolkit_factory = toolkit_factory
    app.state.token_verifier = token_verifier
    app.state.artifact_store = artifact_store
    app.state.workspace_client = workspace_client
    app.state.event_bus = event_bus

    if app.state.settings.cors_allow_origins:
        # 浏览器直连(不经 Vite 代理)时需要;白名单来自配置,不用通配
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app.state.settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        """common 统一错误模型 → {"code","message","details"}(架构 §11)。"""
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    app.include_router(health_router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
