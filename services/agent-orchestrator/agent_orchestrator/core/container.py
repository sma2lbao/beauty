"""进程装配容器(AppContainer):外部客户端、执行图与后台任务的唯一装配点。

为什么集中:create_app 只声明"可注入的外部依赖",真正的装配/启动/逆序关闭
都在这里。顺序即依赖顺序,关闭严格逆序——避免"先关数据库再关 worker"这类
退出期报错;每条资源的所有权(自建 vs 测试注入)决定是否由容器关闭。

装配顺序(与关闭逆序):
    日志/观测/Skills → api_key_verifier → oauth_introspector → token 预算 →
    配额客户端 → 用量上报 → DB session factory → LLM → 记忆嵌入提供方 →
    工具工厂/外部工具工厂 → JWT verifier → 工作区/沙箱 → stdio MCP 池 →
    交付物存储 → 外链下载器 → skill runner → 上下文管线/offload 端口 →
    事件总线 + outbox → run 执行图(Postgres 时可恢复)→ job worker → 记忆清扫

发布契约:容器把每个装配结果同时写到 app.state(测试与依赖注入都从这里取,
见 tests 里的 app.state.* 断言);api 层不直接读 app.state,统一经
api/deps.py 的 get_container 拿到本实例。

本模块保留 AppContainer 类本体(生命周期、发布契约与执行图装配),按装配阶段拆出的
mixin 见同目录 container_assemblies.py / container_auth.py / container_deps.py /
container_tools.py / container_context.py / container_outbox.py。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from common.artifacts import ArtifactStore
from common.auth import JwtVerifier
from common.logging import get_logger, setup_logging
from common.observability import install_observability
from database.base import Base
from database.session import create_session_factory
from events.bus import EventBus
from events.outbox import OutboxDispatcher
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.context.offload import ToolOutputOffloader
from agent_orchestrator.context.pipeline import (
    ContextPipeline,
)
from agent_orchestrator.context.policy import ContextPolicy, policy_from_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.core.container_assemblies import ContainerAssemblyMixin
from agent_orchestrator.core.container_auth import AuthAssemblyMixin
from agent_orchestrator.core.container_context import ContextAssemblyMixin
from agent_orchestrator.core.container_deps import DepsAssemblyMixin
from agent_orchestrator.core.container_outbox import OutboxAssemblyMixin
from agent_orchestrator.core.container_tools import ToolsAssemblyMixin
from agent_orchestrator.graph.builder import build_run_graph
from agent_orchestrator.graph.ports import ToolkitPort
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services.alerts import (
    start_sweeper as start_alert_sweeper,
)
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.interrupts import start_sweeper
from agent_orchestrator.services.jobs_worker import start_worker
from agent_orchestrator.services.knowledge import KnowledgeClient
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.mcp_client import reset_url_guard
from agent_orchestrator.services.mcp_server import (
    ApiKeyVerifier,
    OAuthTokenIntrospector,
)
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.memory_sweeper import (
    start_sweeper as start_memory_sweeper,
)
from agent_orchestrator.services.notifications import (
    start_sweeper as start_notification_sweeper,
)
from agent_orchestrator.services.run_recovery_sweeper import (
    start_sweeper as start_run_recovery_sweeper,
)
from agent_orchestrator.services.run_timeout_sweeper import (
    start_sweeper as start_run_timeout_sweeper,
)
from agent_orchestrator.services.sandbox import Sandbox
from agent_orchestrator.services.skill_runner import SkillRunner
from agent_orchestrator.services.skills import load_skills
from agent_orchestrator.services.subagent_wait_sweeper import (
    start_sweeper as start_subagent_wait_sweeper,
)
from agent_orchestrator.services.tool_client import (
    ToolClient,
)
from agent_orchestrator.services.tools import (
    ToolExtrasFactory,
)
from agent_orchestrator.services.workspace import Workspace

logger = get_logger(__name__)

# 关闭时要逆序取消的后台任务(stop() 按此顺序 cancel + 等退出)
_STOP_TASKS = (
    "worker_task",
    "interrupt_task",
    "run_timeout_task",
    "run_recovery_task",
    "subagent_wait_task",
    "memory_sweep_task",
    "alert_sweep_task",
    "notification_sweep_task",
)

ToolkitFactory = Callable[[str], ToolkitPort]


def _psycopg_conninfo(database_url: str) -> str:
    """SQLAlchemy asyncpg URL → psycopg 连接串(checkpointer 用)。

    psycopg 走 libpq 口径,不认 asyncpg 的 `ssl=` 查询参数:按 `sslmode=` 转写,
    asyncpg 专属的连接参数直接丢弃。
    """
    parts = urlsplit(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    asyncpg_only = {"statement_cache_size", "prepared_statement_cache_size"}
    query = [
        ("sslmode" if key == "ssl" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in asyncpg_only
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


#: 可注入项名称:create_app 收哪些测试替身,容器就发布哪些(测试按名读 app.state)
INJECTED = (
    "llm_client",
    "toolkit_factory",
    "tool_client",
    "knowledge_client",
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


class AppContainer(
    ContainerAssemblyMixin,
    AuthAssemblyMixin,
    DepsAssemblyMixin,
    ToolsAssemblyMixin,
    ContextAssemblyMixin,
    OutboxAssemblyMixin,
):
    """一次进程生命周期内的全部外部依赖与后台任务。"""

    def __init__(
        self,
        app: FastAPI,
        settings: Settings,
        *,
        llm_client: LLMClient | None = None,
        toolkit_factory: ToolkitFactory | None = None,
        tool_client: ToolClient | None = None,
        knowledge_client: KnowledgeClient | None = None,
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
        self.interrupt_task: asyncio.Task[None] | None = None
        # P1-C:run 执行超时 sweeper(独立于中断超时,按总时长兜底 failed)
        self.run_timeout_task: asyncio.Task[None] | None = None
        # P1-C:崩溃续跑 sweeper(执行中但已无进程推进的 run → recover 续跑)
        self.run_recovery_task: asyncio.Task[None] | None = None
        # L2:子代理等待 sweeper(漏唤醒补偿 + 等待超时级联取消)
        self.subagent_wait_task: asyncio.Task[None] | None = None
        self.memory_sweep_task: asyncio.Task[None] | None = None
        self.alert_sweep_task: asyncio.Task[None] | None = None
        # 通知持久化:run 状态变化 → notifications 表(关掉页面也留未读)
        self.notification_sweep_task: asyncio.Task[None] | None = None
        self.settings = settings
        self.llm_client = llm_client
        self.toolkit_factory = toolkit_factory
        self.tool_client = tool_client
        # 引用解析(知识文档)用的独立知识客户端;由 _assemble_tools 按开关补齐
        self.knowledge_client = knowledge_client
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
        # 上下文工程(§7.2):策略快照由 settings 构造一次(不可变),管线与
        # offload 端口在沙箱就绪后的 _assemble_context 里装配并发布
        self.context_policy: ContextPolicy = policy_from_settings(settings)
        self.context_pipeline: ContextPipeline | None = None
        self.tool_offloader: ToolOutputOffloader | None = None
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
        # settings 可能被测试在 create_app 之后替换:策略快照随之重建
        self.context_policy = policy_from_settings(self.settings)
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
        # 告警读取面(docs/design/alerting.md §4):关掉时端点不注册 = 404。
        # 惰性导入:api 层(app.deps)反向依赖本模块,顶层导入会成环。
        from agent_orchestrator.api.internal import install_alerts_endpoint

        install_alerts_endpoint(self._app, settings)
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
        self._assemble_memory()
        # _assemble_tools 自行登记知识客户端 closer(端点解析与 legacy 工具共用)
        await self._assemble_tools()
        await self._assemble_graph_deps()
        self._assemble_context()
        await self._assemble_outbox()
        await self._assemble_graph()

    async def _assemble_graph(self) -> None:
        settings = self.settings
        if settings.database_url.startswith("postgres"):
            # postgres:图线程跨进程可恢复(架构 §6.4;setup 幂等建 checkpoint 表)。
            # 惰性导入:sqlite/dev 环境不需要 psycopg,模块加载零成本
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            pg_url = _psycopg_conninfo(settings.database_url)
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
                    offloader=self.tool_offloader,
                    plan_auto_approve=settings.plan_auto_approve,
                    question_timeout_seconds=settings.question_timeout_seconds,
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
                    offloader=self.tool_offloader,
                    plan_auto_approve=settings.plan_auto_approve,
                    question_timeout_seconds=settings.question_timeout_seconds,
                ),
            )
        if settings.jobs_enabled:
            self.worker_task = start_worker(self)
            # 中断超时依赖 worker 执行 reject/confirm job,故仅在 jobs 开启时启动
            if (
                settings.approval_timeout_seconds > 0
                or settings.confirmation_timeout_seconds > 0
                or settings.question_timeout_seconds > 0
            ):
                self.interrupt_task = start_sweeper(self)
            # run 执行超时(P1-C):生效超时为 0(默认)时不启动,避免空转
            if settings.run_timeout_seconds > 0:
                self.run_timeout_task = start_run_timeout_sweeper(self)
            # 崩溃续跑(P1-C §8):执行中却已无进程推进的 run,接回 checkpoint
            if settings.run_recovery_sweep_interval_seconds > 0:
                self.run_recovery_task = start_run_recovery_sweeper(self)
            # 子代理等待(L2 §6.1):漏唤醒补偿与等待超时都依赖唤醒 job,故同样
            # 只在 jobs 开启时启动
            if (
                settings.subagent_enabled
                and settings.subagent_wait_sweep_interval_seconds > 0
            ):
                self.subagent_wait_task = start_subagent_wait_sweeper(self)
        # 记忆清扫:过期清理 + 相似记忆巩固,独立于 job worker(纯运维后台任务)
        if settings.memory_enabled and settings.memory_sweep_interval_seconds > 0:
            self.memory_sweep_task = start_memory_sweeper(self)
        # 主动告警:纯运维后台任务,只读库;间隔 <= 0 关闭 sweeper
        if settings.alerts_enabled and settings.alerts_sweep_interval_seconds > 0:
            self.alert_sweep_task = start_alert_sweeper(self)
        # 通知持久化:只写本服务库,独立于 web 端是否在线
        if (
            settings.notifications_enabled
            and settings.notifications_sweep_interval_seconds > 0
        ):
            self.notification_sweep_task = start_notification_sweeper(self)

    # ------------------------------------------------------------------ 关闭

    async def stop(self) -> None:
        """严格逆序关闭:worker → 中断/超时 sweeper → outbox → 客户端 → DB。"""
        # MCP 出站校验配置是进程级全局:关停复位,避免同进程的下个容器继承
        reset_url_guard()
        hooks_service.reset_hooks()
        for attr in _STOP_TASKS:
            task = getattr(self, attr)
            if task is None:
                continue
            task.cancel()
            await self._stop_task(attr, task)
            setattr(self, attr, None)
        for name, closer in reversed(self._closers):
            await self._close_resource(name, closer)
        self._closers.clear()
        await self._close_resource("resource stack", self._stack.aclose)

    async def _stop_task(self, name: str, task: asyncio.Task[None]) -> None:
        """等后台任务退出;超时只记警告并放手,关闭不因单个任务无限挂住。

        用 asyncio.wait 而非 await/wait_for:超时后不再等被取消方完成,
        对方吞掉 CancelledError 也不会把 stop() 一起挂住。
        """
        timeout = self.settings.stop_timeout_seconds
        _, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            logger.warning("stop timed out", resource=name, timeout=timeout)
            return
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("stop task failed", resource=name, error=str(exc))

    async def _close_resource(
        self, name: str, closer: Callable[[], Awaitable[None]]
    ) -> None:
        """关闭单个资源(DB 引擎/knowledge 客户端/AsyncExitStack);共用关闭超时。"""
        timeout = self.settings.stop_timeout_seconds
        task = asyncio.create_task(closer(), name=f"stop-{name}")
        _, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            task.cancel()
            logger.warning("close timed out", resource=name, timeout=timeout)
            return
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("close failed", resource=name, error=str(exc))
