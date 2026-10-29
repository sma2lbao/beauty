"""orchestrator 仓储:租户过滤与 404 语义在此声明一次(§6.3/§7)。

事件溯源相关的复杂查询(seq 取号、按类型计数、事件回放)以**显式命名方法**
留在仓储里——业务层不再出现裸 `select`,但也没把 SQL 藏进魔法抽象。

本模块是门面:实现按域拆到同包子模块(运行/会话/MCP/job/审批/附件/消息/事件/
指令/摘要/记忆),这里统一重导出,既有
`from agent_orchestrator.repositories import XRepository` 的 import 路径保持不变。
"""

from __future__ import annotations

from agent_orchestrator.repositories.approval import (
    ApprovalPolicyRepository as ApprovalPolicyRepository,
)
from agent_orchestrator.repositories.attachment import (
    AttachmentRepository as AttachmentRepository,
)
from agent_orchestrator.repositories.constants import (
    CONSUMED as CONSUMED,
)
from agent_orchestrator.repositories.constants import (
    QUESTION_ANSWERED_EVENT as QUESTION_ANSWERED_EVENT,
)
from agent_orchestrator.repositories.constants import (
    QUESTION_REQUIRED_EVENT as QUESTION_REQUIRED_EVENT,
)
from agent_orchestrator.repositories.constants import (
    QUESTION_TIMED_OUT_EVENT as QUESTION_TIMED_OUT_EVENT,
)
from agent_orchestrator.repositories.constants import QUEUED as QUEUED
from agent_orchestrator.repositories.constants import (
    TOOL_CALLED_EVENT as TOOL_CALLED_EVENT,
)
from agent_orchestrator.repositories.conversation import (
    ConversationRepository as ConversationRepository,
)
from agent_orchestrator.repositories.event import (
    RunEventRepository as RunEventRepository,
)
from agent_orchestrator.repositories.instruction import (
    InstructionRepository as InstructionRepository,
)
from agent_orchestrator.repositories.job import JobRepository as JobRepository
from agent_orchestrator.repositories.job import (
    _claim_statement as _claim_statement,
)
from agent_orchestrator.repositories.job import _is_postgres as _is_postgres
from agent_orchestrator.repositories.mcp import (
    MCPServerAuthRepository as MCPServerAuthRepository,
)
from agent_orchestrator.repositories.mcp import (
    MCPServerRepository as MCPServerRepository,
)
from agent_orchestrator.repositories.memory import (
    MemoryRepository as MemoryRepository,
)
from agent_orchestrator.repositories.message import (
    MessageRepository as MessageRepository,
)
from agent_orchestrator.repositories.run import RunRepository as RunRepository
from agent_orchestrator.repositories.summary import (
    ConversationSummaryRepository as ConversationSummaryRepository,
)
from agent_orchestrator.repositories_artifacts import (
    _TEXT_CONTENT_TYPE_EXTRA as _TEXT_CONTENT_TYPE_EXTRA,
)
from agent_orchestrator.repositories_artifacts import (
    REPORT_ARTIFACT_SOURCE as REPORT_ARTIFACT_SOURCE,
)
from agent_orchestrator.repositories_artifacts import (
    ArtifactRepository as ArtifactRepository,
)
from agent_orchestrator.repositories_artifacts import (
    SkillPackageRepository as SkillPackageRepository,
)
from agent_orchestrator.repositories_artifacts import (
    SkillVersionRepository as SkillVersionRepository,
)
from agent_orchestrator.repositories_artifacts import (
    _is_text_artifact_row as _is_text_artifact_row,
)
# 报告件的来源标记:与 services.artifacts.ARTIFACT_REPORT_SOURCE 同值,
# 仓储不反向 import service 层(artifact service 已依赖本模块,反向会循环)。
