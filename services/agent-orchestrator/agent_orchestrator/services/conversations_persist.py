"""conversation 消息持久化与任务上下文(自 conversations.py 拆出)。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from common.artifacts import ArtifactStore
from common.logging import get_logger
from database.session import session_scope
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    MessageRepository,
)
from agent_orchestrator.schemas.runs import RunCreate
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.conversation_targets import (
    MessageTarget as MessageTarget,
)
from agent_orchestrator.services.conversation_targets import (
    require_conversation as require_conversation,
)
from agent_orchestrator.services.conversation_targets import (
    resolve_message_target as resolve_message_target,
)
from agent_orchestrator.services.conversation_targets import (
    resolve_stream_target as resolve_stream_target,
)
from agent_orchestrator.services.conversations_queries import active_run
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.tools import ToolRegistry
from agent_orchestrator.services.workspace import Workspace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]

logger = get_logger(__name__)

DEFAULT_TITLE = "新对话"


MAX_TITLE = 40


def title_from_message(content: str) -> str:
    """用消息首行做标题(过长截断;与前端旧的 titleFromMessage 语义一致)。"""
    first_line = content.strip().split("\n")[0].strip()
    title = first_line or DEFAULT_TITLE
    return f"{title[:MAX_TITLE]}…" if len(title) > MAX_TITLE else title


async def _persist_user_message(
    session: AsyncSession,
    conversation: Conversation,
    content: str,
    *,
    mcp_server_ids: list[str] | None = None,
) -> ChatMessage:
    """落用户消息(首条顺带定题)并顶起会话活跃时间。

    created_at 必须早于随后创建的 run / assistant 回复——多轮历史靠它排序。
    mcp_server_ids 是用户显式引用的 MCP server(空 = 不限制),随消息落库
    以便历史回放时仍能看到"这条消息引用了谁"。
    """
    messages = MessageRepository(session)
    if not await messages.has_any_message(conversation.tenant_id, conversation.id):
        conversation.title = title_from_message(content)
    message = ChatMessage(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        role="user",
        content=content,
        mcp_server_ids=list(mcp_server_ids or []),
    )
    await messages.add(message)
    conversation.updated_at = datetime.now(UTC)
    return message


async def _persist_assistant_message(
    session: AsyncSession, message: ChatMessage, content: str
) -> ChatMessage:
    """落 assistant 回复(直接对话;run 的回复由 runs 服务在终态时投影)。"""
    reply = ChatMessage(
        tenant_id=message.tenant_id,
        conversation_id=message.conversation_id,
        role="assistant",
        content=content,
    )
    await MessageRepository(session).add(reply)
    return reply


async def _task_path(
    session: AsyncSession,
    graph: CompiledStateGraph,
    conversation: Conversation,
    message: ChatMessage,
    content: str,
    *,
    jobs_enabled: bool = False,
    skill: SkillDefinition | None = None,
    attachment_context: str = "",
    mcp_server_ids: list[str] | None = None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
) -> tuple[Run, str]:
    """任务路径:并入进行中的 run 的追加指令,或新开一跑并立即规划。"""
    run = await active_run(session, conversation.tenant_id, conversation.id)
    if run is not None:
        await runs_service.queue_instruction(
            session, conversation.tenant_id, run.id, content + attachment_context
        )
        message.run_id = run.id
        return run, "instruction_queued"
    run = await runs_service.create_run(
        session,
        conversation.tenant_id,
        RunCreate(
            title=title_from_message(content),
            goal=content + attachment_context,
            acceptance_criteria=[],
            conversation_id=conversation.id,
            workspace_id=conversation.workspace_id,
            skill_id=skill.manifest.id if skill else None,
            mcp_server_ids=list(mcp_server_ids or []),
        ),
        skill=skill,
    )
    message.run_id = run.id
    if jobs_enabled:
        await jobs_service.enqueue(session, conversation.tenant_id, "plan", run.id)
    else:
        # 自动放行(无 DANGER 工具)时 plan 一次跑到终态,交付物同样要落库
        await runs_service.plan_run(
            session,
            graph,
            conversation.tenant_id,
            run.id,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
        )
    return run, "run_created"


async def _toolkit_for(
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ExtrasFactory | None,
    tenant_id: str,
) -> ToolRegistry | None:
    """内置工具 + 外部工具(MCP / skill_run)合并成对话可用的注册表。

    extras 是"按调用现取"的(见 main._load_extras),取失败只降级外部工具,
    不让一条对话报错——与 run 路径的降级口径一致。
    """
    toolkit = toolkit_factory(tenant_id) if toolkit_factory else None
    if extras_factory is None:
        return toolkit
    try:
        extras = await extras_factory(tenant_id)
    except Exception as exc:  # noqa: BLE001 - 外部工具故障不阻断对话
        logger.warning(
            "chat tool extras failed",
            tenant_id=tenant_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return toolkit
    if not extras:
        return toolkit
    return toolkit.extended(extras) if toolkit is not None else ToolRegistry(extras)


async def _persist_reply(
    factory: async_sessionmaker[AsyncSession], message: ChatMessage, text: str
) -> ChatMessage:
    """另一段短事务:落 assistant 回复(流式路径用)。"""
    async with session_scope(factory) as session:
        return await _persist_assistant_message(session, message, text)
