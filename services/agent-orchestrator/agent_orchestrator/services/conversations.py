"""conversation 编排业务逻辑:不 import FastAPI,可独立单测。

发一条消息先**分流**(§6.7,判别见 services/triage.py),再按路径落库:

- **chat(简单对话)**:直接由模型答一条,不建 run、不进执行图——不产生副作用、
  不需要审批;用户消息与 assistant 回复都持久化,成为后续的多轮上下文;
- **task(复杂任务)**:消息即 goal(或并入进行中 run 的追加指令队列 §6.5),
  新开一跑时**立即规划**——对话里 Agent 的第一响应就是计划。

取回复有两种姿势,共用同一套分流与上下文口径(所以不会漂):

- post_message:一次拿全文(内部调用 / 脚本用);
- stream_message:产出事件流(meta → delta* → done),对话页据此"边输出边显示"。

流式为什么分两段事务:模型可能吐十几秒,不该一直占着事务——先落用户消息与
分流结论(chat 顺带读多轮上下文),流完再用一段短事务落 assistant 回复。

"进行中"口径与对话页一致:completed/failed/blocked 之外的状态;blocked 需人工
resume,新任务消息应该另开一跑。首条消息自动定题;两条路径的消息都持久化,
构成多轮上下文(runs 建图时带 conversation_history,chat 带此前全部轮次)。

拆分后的实现见同目录兄弟模块:``conversations_context``(chat 上下文/压缩/记忆)、
``conversations_messages``(post_message / stream_message),会话 CRUD、查询与
目标校验分别在同目录 ``conversations_persist`` / ``conversations_queries`` /
``conversation_targets``。本文件保留 ``create_conversation`` 等入口,并原样
重导出全部历史名字——调用方按 ``services.conversations.<name>`` 取用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from common.artifacts import ArtifactStore
from common.logging import get_logger
from database.session import session_scope
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.context.policy import policy_from_settings
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import ConversationRepository, MessageRepository
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import (
    chat,
    compaction,
    history,
    memory_extraction,
    post_commit,
    triage,
)
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.compaction import CompactionResult
from agent_orchestrator.services.conversation_targets import MessageTarget
from agent_orchestrator.services.conversation_targets import (
    require_conversation as require_conversation,
)
from agent_orchestrator.services.conversation_targets import (
    resolve_message_target as resolve_message_target,
)
from agent_orchestrator.services.conversation_targets import (
    resolve_stream_target as resolve_stream_target,
)
from agent_orchestrator.services.conversations_context import (
    _maybe_extract_memories as _maybe_extract_memories,
)
from agent_orchestrator.services.conversations_context import (
    _prepare_chat_context as _prepare_chat_context,
)
from agent_orchestrator.services.conversations_context import (
    _schedule_chat_extraction as _schedule_chat_extraction,
)
from agent_orchestrator.services.conversations_context import (
    _task_compaction as _task_compaction,
)
from agent_orchestrator.services.conversations_messages import (
    post_message as post_message,
)
from agent_orchestrator.services.conversations_messages import (
    stream_message as stream_message,
)
from agent_orchestrator.services.conversations_persist import (
    DEFAULT_TITLE as DEFAULT_TITLE,
)
from agent_orchestrator.services.conversations_persist import MAX_TITLE as MAX_TITLE
from agent_orchestrator.services.conversations_persist import (
    _persist_assistant_message as _persist_assistant_message,
)
from agent_orchestrator.services.conversations_persist import (
    _persist_reply as _persist_reply,
)
from agent_orchestrator.services.conversations_persist import (
    _persist_user_message as _persist_user_message,
)
from agent_orchestrator.services.conversations_persist import _task_path as _task_path
from agent_orchestrator.services.conversations_persist import (
    _toolkit_for as _toolkit_for,
)
from agent_orchestrator.services.conversations_persist import (
    title_from_message as title_from_message,
)
from agent_orchestrator.services.conversations_queries import (
    INACTIVE_STATUSES as INACTIVE_STATUSES,
)
from agent_orchestrator.services.conversations_queries import (
    _backfill_legacy_workspace as _backfill_legacy_workspace,
)
from agent_orchestrator.services.conversations_queries import (
    _default_workspace_id as _default_workspace_id,
)
from agent_orchestrator.services.conversations_queries import active_run as active_run
from agent_orchestrator.services.conversations_queries import (
    decode_cursor as decode_cursor,
)
from agent_orchestrator.services.conversations_queries import (
    delete_conversation as delete_conversation,
)
from agent_orchestrator.services.conversations_queries import (
    encode_cursor as encode_cursor,
)
from agent_orchestrator.services.conversations_queries import (
    list_conversations as list_conversations,
)
from agent_orchestrator.services.conversations_queries import (
    list_messages as list_messages,
)
from agent_orchestrator.services.conversations_queries import (
    rename_conversation as rename_conversation,
)
from agent_orchestrator.services.conversations_queries import (
    resolve_workspace_id as resolve_workspace_id,
)
from agent_orchestrator.services.conversations_queries import (
    set_archived as set_archived,
)
from agent_orchestrator.services.conversations_queries import (
    set_model as set_model,
)
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient, UpstreamError
from agent_orchestrator.services.llm_binding import (
    bind_dialogue_llm,
    model_for_conversation,
)
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRegistry
from agent_orchestrator.services.workspace import Workspace

#: 对外保留的历史名字(调用方与测试按 ``services.conversations.<name>`` 取用)。
__all__ = [
    "ArtifactStore",
    "AsyncIterator",
    "Any",
    "ChatMessage",
    "CompactionResult",
    "CompiledStateGraph",
    "ContextPipeline",
    "FileDownloader",
    "LLMClient",
    "MessageRepository",
    "Run",
    "Settings",
    "SkillDefinition",
    "ToolContext",
    "ToolRegistry",
    "UpstreamError",
    "approval_service",
    "async_sessionmaker",
    "attachments_service",
    "bind_dialogue_llm",
    "bind_tool_context",
    "chat",
    "compaction",
    "history",
    "memory_extraction",
    "memory_service",
    "model_for_conversation",
    "policy_from_settings",
    "post_commit",
    "session_scope",
    "triage",
    "usage_service",
    "DEFAULT_TITLE",
    "INACTIVE_STATUSES",
    "MAX_TITLE",
    "MessageTarget",
    "_backfill_legacy_workspace",
    "_default_workspace_id",
    "_maybe_extract_memories",
    "_persist_assistant_message",
    "_persist_reply",
    "_persist_user_message",
    "_prepare_chat_context",
    "_schedule_chat_extraction",
    "_task_compaction",
    "_task_path",
    "_toolkit_for",
    "active_run",
    "create_conversation",
    "decode_cursor",
    "delete_conversation",
    "encode_cursor",
    "list_conversations",
    "list_messages",
    "logger",
    "post_message",
    "rename_conversation",
    "require_conversation",
    "resolve_message_target",
    "resolve_stream_target",
    "resolve_workspace_id",
    "set_archived",
    "set_model",
    "stream_message",
    "title_from_message",
]

logger = get_logger(__name__)

#: 会话内不再接收消息的 run 状态(completed/failed 是终态;
#: blocked 需人工 resume,新消息另开一跑)


async def create_conversation(
    session: AsyncSession,
    tenant_id: str,
    title: str | None = None,
    *,
    workspace_id: str | None = None,
    model: str | None = None,
    workspace: Workspace | None = None,
) -> Conversation:
    """建对话:标题可空;workspace_id 显式给就校验,不给落租户默认工作区。

    model 是会话级模型偏好(P1-G):为空即"未指定",消息/run 走网关默认。
    """
    resolved = await resolve_workspace_id(workspace, tenant_id, workspace_id)
    conversation = Conversation(
        tenant_id=tenant_id,
        title=title or DEFAULT_TITLE,
        workspace_id=resolved,
        model=model,
    )
    await ConversationRepository(session).add(conversation)
    return conversation
