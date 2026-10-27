"""上下文工程管线(P1-F):策略 + 有序装配 + 大结果 offload + 技能渐进加载。

入口见 docs/design/context-engineering.md:

- policy:`ContextPolicy` / `policy_from_settings`(开关/预算/顺序的唯一真源);
- pipeline:`ContextPipeline` / `ContextRequest` / `ContextSection` / `ContextPlan`;
- agents:沙箱工作区 AGENTS.md 发现与注入;
- skills:SKILL.md 卡片、正文分页与 load_skill 工具;
- offload:大工具结果落盘 + read_offloaded 工具;
- sandbox_files:与 tool-service 对齐的沙箱文件访问(scope_key 规则)。
"""

from __future__ import annotations

from agent_orchestrator.context.agents import (
    AgentsDocument,
    AgentsMdProvider,
    discover_agents_documents,
)
from agent_orchestrator.context.offload import (
    OffloadedOutput,
    ToolOutputOffloader,
    build_read_offloaded_tool,
)
from agent_orchestrator.context.pipeline import (
    ContextPipeline,
    ContextPlan,
    ContextRequest,
    ContextSection,
    MemorySectionProvider,
    SectionProvider,
)
from agent_orchestrator.context.policy import (
    DEFAULT_SECTION_ORDER,
    KNOWN_SECTIONS,
    SECTION_AGENTS_MD,
    SECTION_MEMORY,
    SECTION_SKILLS,
    CompactionPolicy,
    ContextPolicy,
    policy_from_settings,
)
from agent_orchestrator.context.sandbox_files import (
    SCOPE_CONVERSATION_PREFIX,
    SCOPE_RUN_PREFIX,
    SCOPE_TENANT_PREFIX,
    SandboxWorkspaceFiles,
    WorkspaceFiles,
    run_id_from_scope_key,
    scope_key_for,
)
from agent_orchestrator.context.skills import (
    SkillCard,
    SkillCardProvider,
    build_load_skill_tool,
    build_skill_card,
    skill_body_page,
)

__all__ = [
    "DEFAULT_SECTION_ORDER",
    "KNOWN_SECTIONS",
    "SCOPE_CONVERSATION_PREFIX",
    "SCOPE_RUN_PREFIX",
    "SCOPE_TENANT_PREFIX",
    "SECTION_AGENTS_MD",
    "SECTION_MEMORY",
    "SECTION_SKILLS",
    "AgentsDocument",
    "AgentsMdProvider",
    "CompactionPolicy",
    "ContextPlan",
    "ContextPipeline",
    "ContextPolicy",
    "ContextRequest",
    "ContextSection",
    "MemorySectionProvider",
    "OffloadedOutput",
    "SandboxWorkspaceFiles",
    "SectionProvider",
    "SkillCard",
    "SkillCardProvider",
    "ToolOutputOffloader",
    "WorkspaceFiles",
    "build_load_skill_tool",
    "build_read_offloaded_tool",
    "build_skill_card",
    "discover_agents_documents",
    "policy_from_settings",
    "run_id_from_scope_key",
    "scope_key_for",
    "skill_body_page",
]
