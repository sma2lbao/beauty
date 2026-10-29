"""AppContainer 的执行图依赖装配(自 container.py 拆出):LLM 客户端、记忆嵌入
提供方、JWT verifier、工作区/沙箱/stdio MCP 池、交付物存储与 skill runner。
"""

from __future__ import annotations

from common.auth import JwtVerifier

from agent_orchestrator.services.artifacts import build_store
from agent_orchestrator.services.embeddings import (
    build_embedding_provider,
    configure_embedding,
)
from agent_orchestrator.services.file_capture import HttpFileDownloader
from agent_orchestrator.services.llm import GatewayClient
from agent_orchestrator.services.mcp_stdio import StdioSandboxPool
from agent_orchestrator.services.sandbox import SandboxClient
from agent_orchestrator.services.skill_runner import SkillRunner
from agent_orchestrator.services.workspace import WorkspaceClient


class DepsAssemblyMixin:
    """外部客户端/图依赖装配扩展:由 AppContainer 混入。"""

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
                # 网关 BYOK 解析:租户头随上下文注入,内部密钥取本服务配置
                internal_secret=settings.internal_auth_secret,
            )
            self._closers.append(("llm", llm.aclose))
        # 发布解析后的客户端:直接对话(§6.7)与执行图共用同一个;
        # create_app 里存的是"测试注入值",生产路径为 None,这里补齐
        self._publish("llm_client", llm)

    def _assemble_memory(self) -> None:
        """记忆 P2:嵌入提供方全局装配(本地哈希默认;gateway 走 llm-gateway)。

        嵌入是记忆检索/去重/巩固的共同底座,进程内只应有一份配置;provider
        自建的 HTTP 客户端由容器负责关闭(本地哈希无资源,不用登记 closer)。
        """
        provider = build_embedding_provider(self.settings)
        configure_embedding(provider)
        self._publish("embedding_provider", provider)
        aclose = getattr(provider, "aclose", None)
        if aclose is not None:
            self._closers.append(("embedding provider", aclose))

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
