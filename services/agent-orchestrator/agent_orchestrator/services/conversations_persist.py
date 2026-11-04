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

    from agent_orchestrator.context.pipeline import ContextPipeline
    from agent_orchestrator.core.config import Settings
    from agent_orchestrator.graph.ports import ToolkitPort
    from agent_orchestrator.services.compaction import CompactionResult
    from agent_orchestrator.services.tools import ToolSpec

    ToolkitFactory = Callable[[str], ToolkitPort]
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
    return await _persist_assistant_content(
        session,
        tenant_id=message.tenant_id,
        conversation_id=message.conversation_id,
        content=content,
    )


async def _persist_assistant_content(
    session: AsyncSession, *, tenant_id: str, conversation_id: str, content: str
) -> ChatMessage:
    """按租户+对话落一条 assistant 消息(确认链路没有源消息可复制时用)。

    确认端点执行写工具后,结果要作为一条 assistant 消息进入对话历史;那里只有
    conversation_id(触发消息已不在事务里),所以单独开这个入口。
    """
    reply = ChatMessage(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
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
    compaction: CompactionResult | None = None,
    settings: Settings | None = None,
    context_pipeline: ContextPipeline | None = None,
    model: str | None = None,
) -> tuple[Run, str]:
    """任务路径:并入进行中的 run 的追加指令,或新开一跑并立即规划。

    compaction 是建 run 前刚完成的对话压缩(服务层算好传入,这里只透传给
    create_run 记 run.compacted.v1 事件;压缩本身不在这层做,避免重复调模型)。

    context_pipeline 只在同步规划(未入队 job)时透传给 plan_run:起图那一刻
    按策略快照上下文;入队时由 worker 从容器取同一份策略,口径一致。
    """
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
            # 消息级模型覆盖(P1-G);为空时 create_run 会继承会话模型
            model=model,
        ),
        skill=skill,
        compaction=compaction,
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
            settings=settings,
            context=context_pipeline,
        )
    return run, "run_created"


async def _toolkit_for(
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ExtrasFactory | None,
    tenant_id: str,
    *,
    chat: bool = False,
) -> ToolkitPort | None:
    """内置工具 + 外部工具(MCP / skill_run)合并成对话可用的注册表。

    extras 是"按调用现取"的(见 main._load_extras),取失败只降级外部工具,
    不让一条对话报错——与 run 路径的降级口径一致。

    chat=True 时裁剪成对话可见的**功能**工具(§10 决策 / §6.7 就地确认):
    interaction 类工具需要用户回答,只在任务图里暴露;DANGER(不可逆)工具在
    对话里不暴露,引导改用「任务」。risk=read/write 都保留——写操作不再一律
    拒绝,而是先生成 pending 确认、由用户就地确认后执行(见 tool_confirmations)。
    MCP 工具(extras)按租户 auto_approve 映射出的风险等级走同一口径。
    远端工具箱目录拉取失败也只降级、不报错。
    extras 里还有记忆工具(见 docs/design/memory.md §10.2):同样参与裁剪,
    memory_write/memory_delete 在对话里也需要就地确认。
    """
    toolkit = toolkit_factory(tenant_id) if toolkit_factory else None
    if toolkit is not None:
        await _ensure_chat_toolkit(toolkit, tenant_id)
    if chat:
        toolkit = _chat_toolkit(toolkit)
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


async def _ensure_chat_toolkit(toolkit: ToolkitPort, tenant_id: str) -> None:
    """远端工具箱惰性加载目录;失败只降级(没有工具也能正常对话)。"""
    hook = getattr(toolkit, "ensure_ready", None)
    if hook is None:
        return
    try:
        await hook()
    except Exception as exc:  # noqa: BLE001 - 工具服务故障不阻断对话
        logger.warning(
            "chat toolkit catalog failed",
            tenant_id=tenant_id,
            error=f"{type(exc).__name__}: {exc}",
        )


def _tool_kind(toolkit: ToolkitPort, name: str) -> str:
    """工具类型;没有 kind() 的旧工具集一律按 function 处理(向后兼容)。"""
    hook = getattr(toolkit, "kind", None)
    if hook is None:
        return "function"
    try:
        return str(hook(name))
    except Exception:  # noqa: BLE001 - 目录查询失败按普通工具处理
        return "function"


def _chat_toolkit(toolkit: ToolkitPort | None) -> ToolkitPort | None:
    """裁剪出对话可见的**功能**工具(§10.9 + §6.7 就地确认)。

    - interaction 类工具(ask_question)需要用户回答,只随任务图的工具箱
      (with_interaction)暴露;对话侧即便工具箱里带了也必须过滤掉;
    - DANGER 类工具(不可逆/需人工确认)不在对话里暴露,引导走「任务」;
    - READ/WRITE 保留:写操作由确认链路(chat_tool_confirmations)兜底,
      模型发起时只落 pending,不直接执行。
    """
    if toolkit is None:
        return None
    allowed: list[str] = []
    for entry in toolkit.schemas():
        name = entry["function"]["name"]
        if _tool_kind(toolkit, name) != "function":
            continue
        if str(toolkit.risk(name)) == "danger":
            continue
        allowed.append(name)
    return toolkit.restricted(allowed)


async def _persist_reply(
    factory: async_sessionmaker[AsyncSession], message: ChatMessage, text: str
) -> ChatMessage:
    """另一段短事务:落 assistant 回复(流式路径用)。"""
    async with session_scope(factory) as session:
        return await _persist_assistant_message(session, message, text)
