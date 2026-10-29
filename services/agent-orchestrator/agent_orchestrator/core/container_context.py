"""AppContainer 的上下文工程装配(自 container.py 拆出):策略快照、上下文管线与
tool 输出 offload 端口。
"""

from __future__ import annotations

from agent_orchestrator.context.agents import AgentsMdProvider
from agent_orchestrator.context.offload import ToolOutputOffloader
from agent_orchestrator.context.pipeline import (
    ContextPipeline,
    MemorySectionProvider,
    SectionProvider,
)
from agent_orchestrator.context.sandbox_files import SandboxWorkspaceFiles
from agent_orchestrator.context.skills import SkillCardProvider


class ContextAssemblyMixin:
    """上下文工程装配扩展:由 AppContainer 混入。"""

    def _assemble_context(self) -> None:
        """上下文工程(§7.2):只装配端口,不读库、不碰网络。

        policy 是不可变快照,管线/offloader 与沙箱共用同一份 WorkspaceFiles;
        沙箱未启用时没有文件介质:agents_md 段不注册(管线记 not_registered),
        offload 发布 None(执行节点退回原样回填),启动不因此失败。
        """
        policy = self.context_policy
        # 发布策略快照:services 侧事件(run.context.assembled.v1)与测试都取它
        self._publish("context_policy", policy)
        files = (
            SandboxWorkspaceFiles(
                self.sandbox_client,
                ttl_seconds=self.settings.context_sandbox_ttl_seconds,
            )
            if self.sandbox_client is not None
            else None
        )
        pipeline: ContextPipeline | None = None
        if self.settings.context_pipeline_enabled:
            providers: list[SectionProvider] = [
                SkillCardProvider(policy),
                MemorySectionProvider(),
            ]
            if files is not None:
                # 顺序即渲染顺序:工作区约定在前,技能卡片其次,记忆最后
                providers.insert(0, AgentsMdProvider(files, policy))
            pipeline = ContextPipeline(policy, providers=providers)
        self._publish("context_pipeline", pipeline)
        offloader = (
            ToolOutputOffloader(files, policy)
            if files is not None and policy.offload_enabled
            else None
        )
        self._publish("tool_offloader", offloader)
