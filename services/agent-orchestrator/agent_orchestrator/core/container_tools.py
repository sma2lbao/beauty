"""AppContainer 的工具面装配(自 container.py 拆出):tool-service 客户端、内置/远程
工具箱工厂、MCP 与记忆等 extras 工厂。
"""

from __future__ import annotations

from functools import partial

from agent_orchestrator.services.documents import DocumentLibrary
from agent_orchestrator.services.knowledge import KnowledgeClient
from agent_orchestrator.services.token_broker import TokenBroker
from agent_orchestrator.services.tool_client import (
    RemoteToolkit,
    ToolCatalog,
    ToolClient,
)
from agent_orchestrator.services.tools import (
    build_tools,
)


class ToolsAssemblyMixin:
    """工具面装配扩展:由 AppContainer 混入。"""

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

        # 会话级补丁查询与工具执行共用一个客户端:注入的测试替身优先,否则
        # 按开关自建(自建的才登记 closer,注入的不由容器关闭)。
        tool_client = self.tool_client
        if tool_client is None and settings.tool_service_enabled:
            tool_client = ToolClient(
                settings.tool_service_url,
                timeout_seconds=settings.tool_service_timeout_seconds,
                token_provider=broker.token,
            )
            self._closers.append(("tool client", tool_client.aclose))
        self._publish("tool_client", tool_client)

        toolkit_factory = self.toolkit_factory
        knowledge: KnowledgeClient | None = None
        if toolkit_factory is None and settings.tool_service_enabled:
            # 内置工具服务(设计 §12.3):目录与执行都在 tool-service(:8008)。
            # 与旧 tools_enabled 同时开启时以服务为准(迁移期口径,见 §15.5)。
            assert tool_client is not None  # 开关打开时上面必然已建好
            catalog = ToolCatalog()
            toolkit_factory = partial(
                RemoteToolkit, client=tool_client, catalog=catalog
            )
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
            )
        self._publish("toolkit_factory", toolkit_factory)

        policy = self.context_policy
        extras_factory = self.tool_extras_factory
        if extras_factory is None and (
            settings.mcp_enabled
            or settings.skill_execution_enabled
            # 记忆工具也走 extras 通道(§6.7):MCP/技能全关但记忆开着时,
            # 记忆读写工具仍要进执行图与对话的工具面,否则开关形同虚设。
            or settings.memory_enabled
            # 上下文工程(§7.2):load_skill / read_offloaded 同样经 extras
            # 通道下发,否则上面三项全关时这两个工具永远不出现。
            or policy.skill_loading_enabled
            or policy.offload_enabled
        ):
            extras_factory = self._extras_factory()
        self._publish("tool_extras_factory", extras_factory)
        return knowledge
