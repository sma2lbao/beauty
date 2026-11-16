"""流式发消息入口:准备上下文后交给 _stream_turn。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from common.artifacts import ArtifactStore
from database.session import session_scope
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import triage
from agent_orchestrator.services.conversation_targets import require_conversation
from agent_orchestrator.services.conversations_persist import _persist_user_message
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.llm_binding import model_for_conversation
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.workspace import Workspace

from .send import _prepare_turn
from .stream import _stream_turn

if TYPE_CHECKING:  # 仅用于类型标注,避免 services 层与工具层的导入环。
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    #: 外部工具(MCP/skill_run)工厂:按账号现取,与 run 执行图共用
    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]


async def stream_message(
    factory: async_sessionmaker[AsyncSession],
    graph: CompiledStateGraph,
    llm: LLMClient | None,
    account_id: str,
    conversation_id: str,
    content: str,
    *,
    mode: str = triage.AUTO,
    attachment_ids: list[str] | None = None,
    jobs_enabled: bool = False,
    skill: SkillDefinition | None = None,
    mcp_server_ids: list[str] | None = None,
    mcp_reference_note: str = "",
    toolkit_factory: ToolkitFactory | None = None,
    extras_factory: ExtrasFactory | None = None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings | None = None,
    context_pipeline: ContextPipeline | None = None,
    model: str | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """流式发消息:yield (事件名, 载荷)。

    - meta:分流结论(含用户消息,chat 时 run 为空);
    - delta/tool/done/error:本轮执行过程(见 _stream_turn)。

    第一段事务只落用户消息与分流结论(chat 顺带读多轮上下文;task 建 run/排队),
    之后交给 _stream_turn 产增量;重写入口复用同一条流水线。
    """
    # 第一段事务:用户消息 + 分流结论(chat 顺带读多轮上下文;task 建 run/排队)
    run: Run | None = None
    prompt: list[dict[str, Any]] = []
    resolved_model: str | None = None
    async with session_scope(factory) as session:
        conversation = await require_conversation(session, account_id, conversation_id)
        message = await _persist_user_message(
            session, conversation, content, mcp_server_ids=mcp_server_ids
        )
        if attachment_ids:
            await attachments_service.link_attachments(
                session,
                account_id,
                conversation_id,
                message,
                attachment_ids,
                max_count=5,
            )
        attachment_context = ""
        has_images = False
        if attachment_ids:
            attachment_context = await attachments_service.context_for_message(
                session, account_id, message.id
            )
            # 任务路径不注入图片(model-routing §7):带图消息的分流要看这一点
            has_images = await attachments_service.images_in(
                session, account_id, attachment_ids
            )
        resolved_model = model or model_for_conversation(conversation)
        prompt, run, outcome = await _prepare_turn(
            session,
            graph,
            llm,
            account_id,
            conversation,
            message,
            content,
            attachment_context=attachment_context,
            resolved_model=resolved_model,
            has_images=has_images,
            mode=mode,
            jobs_enabled=jobs_enabled,
            skill=skill,
            mcp_server_ids=mcp_server_ids,
            mcp_reference_note=mcp_reference_note,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
            settings=settings,
            context_pipeline=context_pipeline,
            model=model,
        )
    yield "meta", {"mode": outcome, "message": message, "run": run}
    async for event in _stream_turn(
        factory,
        llm,
        account_id=account_id,
        conversation=conversation,
        message=message,
        resolved_model=resolved_model,
        outcome=outcome,
        run=run,
        prompt=prompt,
        mcp_server_ids=mcp_server_ids,
        toolkit_factory=toolkit_factory,
        extras_factory=extras_factory,
        settings=settings,
    ):
        yield event
