"""AppContainer 的装配扩展(自 container.py 拆出):工具/图依赖/outbox。"""

from __future__ import annotations

from datetime import UTC, datetime

from common.logging import get_logger
from database.session import session_scope

from agent_orchestrator.context.offload import build_read_offloaded_tool
from agent_orchestrator.context.skills import build_load_skill_tool
from agent_orchestrator.models import mcp as mcp_models
from agent_orchestrator.models import runs as runs_models
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import mcp_oauth
from agent_orchestrator.services.memory_tools import build_memory_tools
from agent_orchestrator.services.todos import build_todo_tool
from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import (
    ToolExtrasFactory,
    ToolSpec,
)

logger = get_logger(__name__)


class ContainerAssemblyMixin:
    """容器装配扩展:由 AppContainer 混入,便于按域拆分 container.py。"""

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
            # 长期记忆工具(§6.7):跨会话读写偏好/事实,与知识库检索互补
            # (知识库是"文档"检索,记忆是"对话沉淀")。开关关闭即不暴露——
            # 能力即权限边界,与 documents/knowledge 的口径一致。
            if settings.memory_enabled:
                specs += build_memory_tools(
                    factory,
                    max_content_chars=settings.memory_max_content_chars,
                    default_limit=settings.memory_recall_limit,
                )
            # 上下文工程(§7.2):技能正文分页读取与 offload 结果回读都是只读
            # 工具,只对 run 上下文暴露——对话路径没有 run、也不会 offload,
            # 多给两个永远不可用的工具只会污染工具面(§6.5/§6.6)。
            # 用 getattr 容错:测试里的极简容器桩可能没装配上下文属性。
            if ctx is not None and ctx.run_id:
                # 任务清单(M4):清单要 run 上下文(快照列 + 事件),对话路径
                # 没有 run 也没有地方存,所以只在 run 上下文暴露。
                if settings.todo_enabled:
                    specs.append(
                        build_todo_tool(
                            factory,
                            max_items=settings.todo_max_items,
                            max_content_chars=settings.todo_max_content_chars,
                        )
                    )
                policy = getattr(self, "context_policy", None)
                if policy is not None and policy.skill_loading_enabled:
                    specs.append(build_load_skill_tool(factory, policy=policy))
                offloader = getattr(self, "tool_offloader", None)
                if (
                    policy is not None
                    and policy.offload_enabled
                    and offloader is not None
                ):
                    specs.append(build_read_offloaded_tool(offloader))
            return specs

        return _load_extras
