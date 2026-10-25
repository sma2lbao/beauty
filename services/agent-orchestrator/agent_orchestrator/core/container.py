"""进程装配容器(AppContainer):外部客户端、执行图与后台任务的唯一装配点。

为什么集中:create_app 只声明"可注入的外部依赖",真正的装配/启动/逆序关闭
都在这里。顺序即依赖顺序,关闭严格逆序——避免"先关数据库再关 worker"这类
退出期报错;每条资源的所有权(自建 vs 测试注入)决定是否由容器关闭。

装配顺序(与关闭逆序):
    日志/观测/Skills → api_key_verifier → oauth_introspector → token 预算 →
    配额客户端 → 用量上报 → DB session factory → LLM → 工具工厂/外部工具工厂 →
    JWT verifier → 工作区/沙箱 → stdio MCP 池 → 交付物存储 → 外链下载器 →
    skill runner → 事件总线 + outbox → run 执行图(Postgres 时可恢复)→ job worker

发布契约:容器把每个装配结果同时写到 app.state(测试与依赖注入都从这里取,
见 tests 里的 app.state.* 断言);api 层不直接读 app.state,统一经
api/deps.py 的 get_container 拿到本实例。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, suppress
from datetime import UTC, datetime
from functools import partial

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.logging import get_logger, setup_logging
from common.observability import install_observability
from database.base import Base
from database.session import create_session_factory, session_scope
from events.bus import EventBus, InMemoryEventBus
from events.outbox import OutboxDispatcher
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.builder import build_run_graph
from agent_orchestrator.models import mcp as mcp_models
from agent_orchestrator.models import runs as runs_models
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import mcp_oauth
from agent_orchestrator.services import quota as quota_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.artifacts import build_store
from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.file_capture import FileDownloader, HttpFileDownloader
from agent_orchestrator.services.jobs_worker import start_worker
from agent_orchestrator.services.knowledge import KnowledgeClient
from agent_orchestrator.services.llm import GatewayClient, LLMClient
from agent_orchestrator.services.mcp_server import (
    ApiKeyVerifier,
    OAuthTokenIntrospector,
)
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.outbox import record_run_event, register_consumers
from agent_orchestrator.services.sandbox import Sandbox, SandboxClient
from agent_orchestrator.services.skill_runner import SkillRunner
from agent_orchestrator.services.skills import load_skills
from agent_orchestrator.services.token_broker import TokenBroker
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import (
    ToolExtrasFactory,
    ToolRegistry,
    ToolSpec,
    build_tools,
)
from agent_orchestrator.services.workspace import Workspace, WorkspaceClient

logger = get_logger(__name__)

ToolkitFactory = Callable[[str], ToolRegistry]

#: 可注入项名称:create_app 收哪些测试替身,容器就发布哪些(测试按名读 app.state)
INJECTED = (
    "llm_client",
    "toolkit_factory",
    "tool_extras_factory",
    "token_verifier",
    "artifact_store",
    "workspace_client",
    "sandbox_client",
    "skill_runner",
    "mcp_stdio_pool",
    "event_bus",
    "api_key_verifier",
    "oauth_introspector",
)


class AppContainer:
    """一次进程生命周期内的全部外部依赖与后台任务。"""

    def __init__(
        self,
        app: FastAPI,
        settings: Settings,
        *,
        llm_client: LLMClient | None = None,
        toolkit_factory: ToolkitFactory | None = None,
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
    ) -> None:
        self._app = app
        # 只持有 state 对象本身,装配结果统一经 _publish 同步写回 app.state
        self._state = app.state
        self._closers: list[tuple[str, Callable[[], Awaitable[None]]]] = []
        self._stack = AsyncExitStack()
        self.session_factory: async_sessionmaker[AsyncSession] | None = None
        self.run_graph = None
        self.worker_task: asyncio.Task[None] | None = None
        self.settings = settings
        self.llm_client = llm_client
        self.toolkit_factory = toolkit_factory
        self.tool_extras_factory = tool_extras_factory
        self.token_verifier = token_verifier
        self.artifact_store = artifact_store
        self.workspace_client = workspace_client
        self.sandbox_client = sandbox_client
        self.skill_runner = skill_runner
        self.mcp_stdio_pool = mcp_stdio_pool
        self.event_bus = event_bus
        self.api_key_verifier = api_key_verifier
        self.oauth_introspector = oauth_introspector
        self.artifact_downloader: FileDownloader | None = None
        self.skills = None
        self.outbox_recorder = None
        self.outbox_dispatcher: OutboxDispatcher | None = None
        self.outbox_consumers = None

    # ------------------------------------------------------------------ 发布

    def install(self) -> None:
        """create_app 阶段:发布容器自身、settings 与全部注入项(含 None)。"""
        self._publish("container", self)
        self._publish("settings", self.settings)
        for name in INJECTED:
            self._publish(name, getattr(self, name))

    def _publish(self, name: str, value: object) -> None:
        """内存属性 + app.state 双写:前者给依赖注入,后者兼容既有测试。"""
        setattr(self, name, value)
        setattr(self._state, name, value)

    def _adopt(self) -> None:
        """启动前重新采信 app.state:允许测试在 create_app 之后替换注入项。"""
        state = self._state
        self.settings = state.settings
        for name in INJECTED:
            setattr(self, name, getattr(state, name, None))
        self.artifact_downloader = getattr(state, "artifact_downloader", None)

    # ------------------------------------------------------------------ 启动

    async def start(self) -> None:
        """按依赖顺序装配;任何一步失败都逆序回收已建资源后再抛。"""
        self._adopt()
        try:
            await self._assemble()
        except BaseException:
            await self.stop()
            raise

    async def _assemble(self) -> None:
        settings = self.settings
        setup_logging(settings)
        # Sentry(P1)与 /internal/metrics(P2):DSN 为空/开关关闭时都是 no-op
        install_observability(self._app, settings)
        self._publish("skills", load_skills(settings.skills_dir or None))

        await self._assemble_auth()
        self._assemble_budget()
        factory = create_session_factory(settings.database_url)
        self._publish("session_factory", factory)
        self._closers.append(("db engine", factory.engine.dispose))
        if settings.auto_create_tables:
            # dev/test 直建表;生产一律 alembic 迁移
            async with factory.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        await self._assemble_llm()
        knowledge = await self._assemble_tools()
        if knowledge is not None:
            self._closers.append(("knowledge client", knowledge.aclose))
        await self._assemble_graph_deps()
        await self._assemble_outbox()
        await self._assemble_graph()

    async def _assemble_auth(self) -> None:
        settings = self.settings
        key_verifier = self.api_key_verifier
        owns_key_verifier = False
        if key_verifier is None and settings.mcp_server_enabled:
            key_verifier = ApiKeyVerifier(
                base_url=settings.account_service_url,
                internal_secret=settings.internal_auth_secret,
            )
            owns_key_verifier = True
        self._publish("api_key_verifier", key_verifier)
        if owns_key_verifier and key_verifier is not None:
            self._closers.append(("api key verifier", key_verifier.aclose))

        introspector = self.oauth_introspector
        owns_introspector = False
        if introspector is None and settings.mcp_server_enabled:
            introspector = OAuthTokenIntrospector(
                base_url=settings.account_service_url,
                internal_secret=settings.internal_auth_secret,
            )
            owns_introspector = True
        self._publish("oauth_introspector", introspector)
        if owns_introspector and introspector is not None:
            self._closers.append(("oauth introspector", introspector.aclose))

    def _assemble_budget(self) -> None:
        settings = self.settings
        # token 预算(P0-3):进程级固定,0 = 不限制
        usage_service.configure_budget(settings.run_token_budget)

        # 配额快照(P0-2):建 run 前读 account 的租户配额;密钥缺失即关闭
        if quota_service.client() is None and quota_service.configure_client(
            base_url=settings.account_service_url,
            internal_secret=settings.internal_auth_secret,
        ):
            self._closers.append(("quota client", quota_service.close_client))

        # 用量上报(P0-1):密钥缺失即关闭(不裸调),测试默认零网络
        if usage_service.reporter() is None and usage_service.configure_reporter(
            base_url=settings.account_service_url,
            internal_secret=settings.internal_auth_secret,
        ):
            self._closers.append(("usage reporter", usage_service.close_reporter))

    async def _assemble_llm(self) -> None:
        settings = self.settings
        llm = self.llm_client
        if llm is None and settings.llm_enabled:
            llm = GatewayClient(
                base_url=settings.llm_gateway_url,
                timeout_seconds=settings.llm_timeout_seconds,
                model=settings.llm_model,
                retry_attempts=settings.llm_retry_attempts,
                retry_backoff_seconds=settings.llm_retry_backoff_seconds,
            )
            self._closers.append(("llm", llm.aclose))
        # 发布解析后的客户端:直接对话(§6.7)与执行图共用同一个;
        # create_app 里存的是"测试注入值",生产路径为 None,这里补齐
        self._publish("llm_client", llm)

    async def _assemble_tools(self) -> KnowledgeClient | None:
        settings = self.settings
        # 服务间 token(§5):内部调用一律经 TokenBroker 换标准 JWT——租户来自
        # 签名 claim,不再有 X-Tenant-Id 裸调路径;密钥未配置时在换发处明确报错
        broker = TokenBroker(
            base_url=settings.auth_issuer,  # 签发方即 account-service
            internal_secret=settings.internal_auth_secret,
        )
        self.broker = broker
        self._closers.append(("token broker", broker.aclose))
        self._token_provider = broker.token

        toolkit_factory = self.toolkit_factory
        knowledge: KnowledgeClient | None = None
        if toolkit_factory is None and settings.tools_enabled:
            documents = (
                DocumentLibrary(settings.documents_root, settings.documents_max_bytes)
                if settings.documents_enabled
                else None
            )
            knowledge = (
                KnowledgeClient(
                    settings.knowledge_service_url,
                    settings.knowledge_timeout_seconds,
                    token_provider=broker.token,
                )
                if settings.knowledge_enabled
                else None
            )
            toolkit_factory = partial(
                build_tools,
                documents=documents,
                knowledge=knowledge,
                code_roots=settings.code_workspace_roots,
            )
        self._publish("toolkit_factory", toolkit_factory)

        extras_factory = self.tool_extras_factory
        if extras_factory is None and (
            settings.mcp_enabled or settings.skill_execution_enabled
        ):
            extras_factory = self._extras_factory()
        self._publish("tool_extras_factory", extras_factory)
        return knowledge

    def _extras_factory(self) -> ToolExtrasFactory:
        """外部工具(MCP / skill_run)工厂:调用边界才解析,复用容器里的依赖。"""

        async def _load_extras(tenant_id: str) -> list[ToolSpec]:
            """run 绑定了可执行技能包 → skill_run;有 MCP → 追加 MCP 工具。

            skill 与 MCP 互不阻断:MCP 故障只降级 MCP 工具;chat 路径没有
            run_id,自然看不到 skill_run。
            """
            settings = self.settings
            factory = self.session_factory
            assert factory is not None
            specs: list[ToolSpec] = []
            runner = self.skill_runner
            ctx = current_tool_context()
            # 显式引用的 MCP server:对话路径从 contextvar 读;run 路径读 run 快照
            selected: tuple[str, ...] | None = None
            if ctx is not None and ctx.mcp_server_ids:
                selected = ctx.mcp_server_ids
            if ctx is not None and ctx.run_id:
                async with session_scope(factory) as session:
                    run = await session.get(runs_models.Run, ctx.run_id)
                    if run is not None and run.tenant_id == tenant_id:
                        if runner is not None and run.skill_object_key:
                            specs.append(runner.spec(run))
                        if run.mcp_server_ids:
                            selected = tuple(run.mcp_server_ids)
            if settings.mcp_enabled:

                async def _mark_mcp_auth_required(server_id: str, detail: str) -> None:
                    """调用期 401 落库:/mcp 页不必手动测试也能看到"需要授权"。"""
                    async with session_scope(factory) as session:
                        server = await session.get(mcp_models.MCPServer, server_id)
                        if server is None or server.tenant_id != tenant_id:
                            return
                        server.last_status = "auth_required"
                        server.last_error = detail[:500]
                        server.last_checked_at = datetime.now(UTC)

                async def _mcp_token(server_id: str, force: bool) -> str | None:
                    """调用前解析 OAuth token(过期自动刷新;401 时 force 刷新)。"""
                    if not settings.mcp_oauth_enabled:
                        return None
                    async with session_scope(factory) as session:
                        return await mcp_oauth.access_token(
                            session,
                            tenant_id,
                            server_id,
                            settings=settings,
                            secret=settings.mcp_secret,
                            force_refresh=force,
                        )

                try:
                    async with session_scope(factory) as session:
                        # stdio MCP 的沙箱池在启动后段才装配,这里按调用时取值
                        specs += await mcp_service.tool_specs(
                            session,
                            tenant_id,
                            secret=settings.mcp_secret,
                            pool=self.mcp_stdio_pool,
                            on_auth_required=_mark_mcp_auth_required,
                            token_provider=_mcp_token,
                            server_ids=selected,
                        )
                except Exception as exc:  # noqa: BLE001 - MCP 故障不阻断 skill
                    logger.warning(
                        "mcp tool specs failed",
                        tenant_id=tenant_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            return specs

        return _load_extras

    async def _assemble_graph_deps(self) -> None:
        settings = self.settings
        verifier = self.token_verifier
        owns_verifier = False
        if verifier is None and settings.auth_enabled:
            verifier = JwtVerifier(
                issuer=settings.auth_issuer,
                audience=settings.auth_audience,
                jwks_url=settings.auth_jwks_url,
                tenant_claim=settings.auth_tenant_claim,
            )
            owns_verifier = True
        self._publish("token_verifier", verifier)
        if owns_verifier and verifier is not None:
            self._closers.append(("token verifier", verifier.aclose))

        # 交付物镜像到工作区(§7):独立于工具箱开关
        workspace = self.workspace_client
        if workspace is None and settings.workspace_enabled:
            workspace = WorkspaceClient(
                settings.workspace_service_url,
                settings.workspace_timeout_seconds,
                token_provider=self._token_provider,
            )
        self._publish("workspace_client", workspace)

        # 沙箱客户端(P1):测试注入优先;未开启时不起客户端、不碰网络
        sandbox = self.sandbox_client
        if sandbox is None and settings.sandbox_enabled:
            sandbox = SandboxClient(
                settings.sandbox_service_url,
                settings.sandbox_timeout_seconds,
                token_provider=self._token_provider,
            )
            self._closers.append(("sandbox client", sandbox.aclose))
        self._publish("sandbox_client", sandbox)

        # stdio MCP 沙箱池:沙箱客户端就绪后装配;两个开关都开才建池
        pool = self.mcp_stdio_pool
        if pool is None and settings.mcp_stdio_enabled and sandbox is not None:
            pool = StdioSandboxPool(
                sandbox,
                ttl_seconds=settings.mcp_stdio_sandbox_ttl_seconds,
                network=settings.mcp_stdio_network,
                python_image=settings.mcp_stdio_python_image,
                node_image=settings.mcp_stdio_node_image,
                timeout_seconds=int(settings.sandbox_timeout_seconds),
            )
        self._publish("mcp_stdio_pool", pool)

        # 交付物存储(§7 阶段 B):按配置选后端(database 模式为 None)
        store = self.artifact_store
        if store is None:
            store = build_store(settings)
        self._publish("artifact_store", store)

        # 外链文件抓取(§7 阶段 B):关闭时不建下载器(只落报告件,不碰网络)
        downloader = self.artifact_downloader
        if downloader is None and settings.artifact_download_enabled:
            downloader = HttpFileDownloader(
                timeout_seconds=settings.artifact_download_timeout_seconds,
                max_bytes=settings.artifact_download_max_bytes,
            )
        self._publish("artifact_downloader", downloader)

        # 上传技能包执行(P3-C):sandbox/workspace 都在才装配(缺任一不暴露)
        runner = self.skill_runner
        if (
            runner is None
            and settings.skill_execution_enabled
            and sandbox is not None
            and workspace is not None
        ):
            runner = SkillRunner(
                self.session_factory,
                sandbox=sandbox,
                workspace=workspace,
                store=store,
                settings=settings,
            )
        self._publish("skill_runner", runner)

    async def _assemble_outbox(self) -> None:
        settings = self.settings
        bus: EventBus = self.event_bus or InMemoryEventBus()
        self._publish("event_bus", bus)
        recorder = None
        dispatcher: OutboxDispatcher | None = None
        self._outbox_stop: asyncio.Event | None = None
        ingest_knowledge: KnowledgeClient | None = None
        if settings.outbox_enabled:
            recorder = record_run_event
            if settings.knowledge_ingest_enabled:
                ingest_knowledge = KnowledgeClient(
                    settings.knowledge_service_url,
                    settings.knowledge_timeout_seconds,
                    token_provider=self._token_provider,
                )
                self._closers.append(("ingest knowledge", ingest_knowledge.aclose))
                consumers = register_consumers(
                    bus,
                    knowledge=ingest_knowledge,
                    session_factory=self.session_factory,
                    store=self.artifact_store,
                )
                self._publish("outbox_consumers", consumers)
            dispatcher = OutboxDispatcher(
                self.session_factory,
                bus,
                batch_size=settings.outbox_batch_size,
                max_attempts=settings.outbox_max_attempts,
            )
            self._outbox_stop = asyncio.Event()
            self._outbox_task = asyncio.create_task(
                dispatcher.run_forever(
                    interval_seconds=settings.outbox_dispatch_interval_seconds,
                    stop=self._outbox_stop,
                )
            )
            self._closers.append(("outbox task", self._stop_outbox))
        self._publish("outbox_recorder", recorder)
        self._publish("outbox_dispatcher", dispatcher)

    async def _stop_outbox(self) -> None:
        if self._outbox_stop is not None:
            self._outbox_stop.set()
        task = getattr(self, "_outbox_task", None)
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _assemble_graph(self) -> None:
        settings = self.settings
        if settings.database_url.startswith("postgres"):
            # postgres:图线程跨进程可恢复(架构 §6.4;setup 幂等建 checkpoint 表)。
            # 惰性导入:sqlite/dev 环境不需要 psycopg,模块加载零成本
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            pg_url = settings.database_url.replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
            saver = await self._stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(pg_url)
            )
            await saver.setup()
            self._publish(
                "run_graph",
                build_run_graph(
                    checkpointer=saver,
                    llm=self.llm_client,
                    toolkit_factory=self.toolkit_factory,
                    mcp_tools_factory=self.tool_extras_factory,
                    plan_auto_approve=settings.plan_auto_approve,
                ),
            )
        else:
            # sqlite/dev:进程内 checkpointer
            self._publish(
                "run_graph",
                build_run_graph(
                    llm=self.llm_client,
                    toolkit_factory=self.toolkit_factory,
                    mcp_tools_factory=self.tool_extras_factory,
                    plan_auto_approve=settings.plan_auto_approve,
                ),
            )
        if settings.jobs_enabled:
            self.worker_task = start_worker(self)

    # ------------------------------------------------------------------ 关闭

    async def stop(self) -> None:
        """严格逆序关闭:worker → outbox → 各客户端 → DB → checkpointer。"""
        if self.worker_task is not None:
            self.worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker_task
            self.worker_task = None
        for name, closer in reversed(self._closers):
            try:
                await closer()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不能拖垮其余资源
                logger.warning("close failed", resource=name, error=str(exc))
        self._closers.clear()
        await self._stack.aclose()
