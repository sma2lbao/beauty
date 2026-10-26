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
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from common.artifacts import ArtifactStore
from common.logging import get_logger
from database.session import session_scope
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    ConversationRepository,
    MessageRepository,
)
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
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient, UpstreamError
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.tool_context import (
    ToolContext,
    bind_tool_context,
)
from agent_orchestrator.services.tools import ToolRegistry
from agent_orchestrator.services.workspace import Workspace

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    #: 外部工具(MCP/skill_run)工厂:按租户现取,与 run 执行图共用
    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]

logger = get_logger(__name__)

#: 会话内不再接收消息的 run 状态(completed/failed 是终态;
#: blocked 需人工 resume,新消息另开一跑)


async def _prepare_chat_context(
    session: AsyncSession,
    llm: LLMClient,
    message: ChatMessage,
    conversation: Conversation,
    settings: Settings | None,
) -> chat.ChatPromptContext:
    """直接对话的上下文:压缩摘要 + 长期记忆召回(§6.7 记忆模块)。

    压缩只在超过阈值时真正调模型,失败/未开启时退回"近期原文"的老口径;
    记忆召回按工作区隔离,召回失败只降级为空——上下文增强不能阻断对话。
    """
    rows = await compaction.rows_before_current(
        session, message.tenant_id, message.conversation_id, message
    )
    summary, covered_until = await compaction.summary_for(
        session, message.tenant_id, message.conversation_id
    )
    if settings is not None and settings.context_compaction_enabled:
        result = await compaction.compact_conversation(
            session,
            message.tenant_id,
            message.conversation_id,
            llm,
            message=message,
            threshold_tokens=settings.context_compact_threshold_tokens,
            keep_recent=settings.context_keep_recent_messages,
            summary_max_chars=settings.context_summary_max_chars,
        )
        if result is not None:
            summary, covered_until = result.summary, result.covered_until
    memory_block = ""
    if settings is not None and settings.memory_enabled:
        convention = await memory_service.convention_block(
            session,
            message.tenant_id,
            workspace_id=conversation.workspace_id,
            max_chars=settings.memory_convention_max_chars,
        )
        recall = await memory_service.recall_block(
            session,
            message.tenant_id,
            message.content,
            workspace_id=conversation.workspace_id,
            limit=settings.memory_recall_limit,
            max_chars=settings.memory_recall_max_chars,
        )
        memory_block = "\n\n".join(part for part in (convention, recall) if part)
    return chat.ChatPromptContext(
        rows=history.filter_after(rows, covered_until),
        summary=summary,
        memory_block=memory_block,
    )


async def _maybe_extract_memories(
    session: AsyncSession,
    llm: LLMClient | None,
    *,
    tenant_id: str,
    conversation_id: str,
    workspace_id: str | None,
    settings: Settings | None,
) -> None:
    """chat 回复落库后按累计条数节流地自动抽取长期记忆(P2)。

    节流语义:每累计 memory_extraction_min_messages 条消息抽一次(用户与
    assistant 都计入),同一对话不会每轮都多调一次模型;抽取失败只记 warning,
    绝不影响已经产出的回复(增强能力不能反噬主链路)。
    """
    if llm is None or settings is None:
        return
    if not (settings.memory_enabled and settings.memory_auto_extract_enabled):
        return
    min_messages = settings.memory_extraction_min_messages
    if min_messages <= 0:
        return
    try:
        repository = MessageRepository(session)
        count = await repository.count_for_conversation(tenant_id, conversation_id)
        if count % min_messages != 0:
            return
        messages = await repository.list_for_conversation(tenant_id, conversation_id)
        await memory_extraction.extract_from_messages(
            session,
            llm,
            tenant_id=tenant_id,
            messages=messages,
            workspace_id=workspace_id,
            source_type="chat",
            source_id=conversation_id,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - 记忆抽取不能影响回复
        logger.warning(
            "memory auto extraction failed",
            conversation_id=conversation_id,
            error=str(exc),
        )


def _schedule_chat_extraction(
    session: AsyncSession,
    llm: LLMClient | None,
    *,
    tenant_id: str,
    conversation_id: str,
    workspace_id: str | None,
    settings: Settings | None,
) -> bool:
    """有提交后回调能力时,把 chat 抽取推迟到事务提交后(P1-2)。

    返回 True 表示已登记回调、调用方不要再同步等待;返回 False 表示当前会话
    没有运行时绑定(服务层直调/单测),调用方保留同步语义。
    """
    runtime = post_commit.runtime_of(session)
    if runtime is None or llm is None or settings is None:
        return False
    if not (settings.memory_enabled and settings.memory_auto_extract_enabled):
        return False

    async def _extract() -> None:
        async with session_scope(runtime.session_factory) as extract_session:
            await _maybe_extract_memories(
                extract_session,
                llm,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                settings=settings,
            )

    post_commit.register_after_commit(session, _extract)
    return True


async def _task_compaction(
    session: AsyncSession,
    llm: LLMClient | None,
    conversation: Conversation,
    message: ChatMessage,
    settings: Settings | None,
) -> CompactionResult | None:
    """任务路径建 run 前的压缩(已有进行中 run 时并入指令队列,不需要)。

    压缩放在这里而不是执行图里:建 run 的请求边界有 LLM 客户端,压缩只发生
    一次;执行图只读已落库的摘要(见 run_lifecycle._conversation_history)。
    """
    if settings is None or not settings.context_compaction_enabled or llm is None:
        return None
    if await active_run(session, conversation.tenant_id, conversation.id) is not None:
        return None
    return await compaction.compact_conversation(
        session,
        conversation.tenant_id,
        conversation.id,
        llm,
        message=message,
        threshold_tokens=settings.context_compact_threshold_tokens,
        keep_recent=settings.context_keep_recent_messages,
        summary_max_chars=settings.context_summary_max_chars,
    )


async def create_conversation(
    session: AsyncSession,
    tenant_id: str,
    title: str | None = None,
    *,
    workspace_id: str | None = None,
    workspace: Workspace | None = None,
) -> Conversation:
    """建对话:标题可空;workspace_id 显式给就校验,不给落租户默认工作区。"""
    resolved = await resolve_workspace_id(workspace, tenant_id, workspace_id)
    conversation = Conversation(
        tenant_id=tenant_id,
        title=title or DEFAULT_TITLE,
        workspace_id=resolved,
    )
    await ConversationRepository(session).add(conversation)
    return conversation


async def post_message(
    session: AsyncSession,
    graph: CompiledStateGraph,
    llm: LLMClient | None,
    tenant_id: str,
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
) -> tuple[ChatMessage, Run | None, ChatMessage | None, str]:
    """发消息(一次拿全文):先分流(§6.7),再按 chat / task 路径持久化。

    返回 (用户消息, run|None, assistant 直接回复|None, 处理方式);
    处理方式 ∈ {"chat", "run_created", "instruction_queued"}。

    chat 路径携带只读工具(toolkit_factory + extras_factory):对话内
    function-calling(§6.8);外部工具(MCP)里 requires_confirmation 的会被
    chat.run_tool_loop 拒绝并提示改用「任务」(对话没有审批流,§6.7)。
    """
    conversation = await require_conversation(session, tenant_id, conversation_id)
    message = await _persist_user_message(
        session, conversation, content, mcp_server_ids=mcp_server_ids
    )
    if attachment_ids:
        await attachments_service.link_attachments(
            session,
            tenant_id,
            conversation_id,
            message,
            attachment_ids,
            max_count=5,
        )
    attachment_context = ""
    if attachment_ids:
        attachment_context = await attachments_service.context_for_message(
            session, tenant_id, message.id
        )
    decided = await triage.decide(content, llm, mode=mode)
    # llm 为空时 decide 不会给出 chat(显式 chat 无模型会 409),这里判空只为收窄类型
    if decided == triage.CHAT and llm is not None:
        # 工具 handler 执行时读得到对话上下文(stdio MCP 沙箱按对话复用);
        # mcp_server_ids 让外部工具工厂只装配被引用的 server
        with bind_tool_context(
            ToolContext(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                workspace_id=conversation.workspace_id,
                mcp_server_ids=tuple(mcp_server_ids or ()),
            )
        ):
            toolkit = await _toolkit_for(
                toolkit_factory, extras_factory, tenant_id, read_only=True
            )
            policies = await approval_service.load_policies(session, tenant_id)
            context = await _prepare_chat_context(
                session, llm, message, conversation, settings
            )
            answer = await chat.answer(
                session,
                llm,
                message,
                toolkit,
                reference_note=mcp_reference_note,
                policies=policies,
                context=context,
            )
        reply = await _persist_assistant_message(session, message, answer)
        # 有会话边界时推迟到提交后抽取(响应不等二次模型调用);服务层直调
        # (无 runtime 绑定)保持同步语义,行为与之前一致
        if not _schedule_chat_extraction(
            session,
            llm,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            workspace_id=conversation.workspace_id,
            settings=settings,
        ):
            await _maybe_extract_memories(
                session,
                llm,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                workspace_id=conversation.workspace_id,
                settings=settings,
            )
        return message, None, reply, triage.CHAT
    compaction_result = await _task_compaction(
        session, llm, conversation, message, settings
    )
    run, outcome = await _task_path(
        session,
        graph,
        conversation,
        message,
        content,
        jobs_enabled=jobs_enabled,
        skill=skill,
        attachment_context=attachment_context,
        mcp_server_ids=mcp_server_ids,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=max_download_files,
        compaction=compaction_result,
        settings=settings,
    )
    return message, run, None, outcome


async def stream_message(
    factory: async_sessionmaker[AsyncSession],
    graph: CompiledStateGraph,
    llm: LLMClient | None,
    tenant_id: str,
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
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """流式发消息:yield (事件名, 载荷)。

    - meta:分流结论(含用户消息,chat 时 run 为空);
    - delta:正文增量(仅 chat;工具轮也是真流式,逐段原样转发);
    - tool:chat 内只读工具调用(对话内 function-calling,§6.8);
    - done:收尾(chat 带落库后的 assistant 回复);
    - error:失败(流已按 200 开头,只能用帧报错)。

    工具轮的正文不再"跑完整个工具循环再按字补发":每轮模型输出边产出边进
    delta 帧,工具事件夹在轮次之间;中间轮次的过渡说明同样实时下发,并随
    parts 一起落库,最终 assistant 回复 = 各轮正文拼接(与客户端看到的逐帧
    内容完全一致)。

    客户端中途断开时,已产出的增量仍会落库(不丢半截回复);全部失败则不留
    assistant 消息,由对话页展示错误。
    """
    # 第一段事务:用户消息 + 分流结论(chat 顺带读多轮上下文;task 建 run/排队)
    run: Run | None = None
    prompt: list[dict[str, str]] = []
    conversation_workspace_id: str | None = None
    async with session_scope(factory) as session:
        conversation = await require_conversation(session, tenant_id, conversation_id)
        conversation_workspace_id = conversation.workspace_id
        message = await _persist_user_message(
            session, conversation, content, mcp_server_ids=mcp_server_ids
        )
        if attachment_ids:
            await attachments_service.link_attachments(
                session,
                tenant_id,
                conversation_id,
                message,
                attachment_ids,
                max_count=5,
            )
        attachment_context = ""
        if attachment_ids:
            attachment_context = await attachments_service.context_for_message(
                session, tenant_id, message.id
            )
        decided = await triage.decide(content, llm, mode=mode)
        if decided == triage.CHAT and llm is not None:
            context = await _prepare_chat_context(
                session, llm, message, conversation, settings
            )
            prompt = await chat.prompt(
                session,
                message,
                reference_note=mcp_reference_note,
                context=context,
            )
            outcome = triage.CHAT
        else:
            compaction_result = await _task_compaction(
                session, llm, conversation, message, settings
            )
            run, outcome = await _task_path(
                session,
                graph,
                conversation,
                message,
                content,
                jobs_enabled=jobs_enabled,
                skill=skill,
                attachment_context=attachment_context,
                mcp_server_ids=mcp_server_ids,
                store=store,
                workspace=workspace,
                downloader=downloader,
                max_download_files=max_download_files,
                compaction=compaction_result,
                settings=settings,
            )
    yield "meta", {"mode": outcome, "message": message, "run": run}
    if run is not None or llm is None:
        # 任务路径的进展由 run 自己的事件流呈现,这里不重复
        yield "done", {"mode": outcome, "reply": None}
        return

    # 第二段:流式产出增量(不占事务);顺带采集本轮用量(P0-1,run_id 为空)
    collector = usage_service.UsageCollector()
    usage_token = usage_service.bind(collector)
    parts: list[str] = []
    failure: UpstreamError | None = None
    reply: ChatMessage | None = None
    try:
        # 对话内工具执行的来源上下文(stdio MCP 沙箱按对话复用);
        # 生成器被 aclose 时 with 会退出,reset contextvar,不跨请求泄漏
        with bind_tool_context(
            ToolContext(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                workspace_id=conversation.workspace_id,
                mcp_server_ids=tuple(mcp_server_ids or ()),
            )
        ):
            toolkit = await _toolkit_for(
                toolkit_factory, extras_factory, tenant_id, read_only=True
            )
            if toolkit is None or len(toolkit) == 0:
                # 无工具:保持原路径——真正的流式增量(边产出边显示)
                async for delta in llm.stream(prompt):
                    parts.append(delta)
                    yield "delta", {"text": delta}
            else:
                # 对话内 function-calling:每轮正文真流式下发、工具事件实时转发;
                # 中间轮(模型过渡说明)的正文也下发并落库,不再只保留最后一轮。
                # 第一段事务已在 yield meta 前结束,此处必须开新事务:
                # 复用已 close 的 session 会隐式 autobegin 再签出连接,无人归还
                async with session_scope(factory) as policy_session:
                    policies = await approval_service.load_policies(
                        policy_session, tenant_id
                    )
                async for kind, payload in chat.run_tool_loop(
                    llm, prompt, toolkit, policies=policies, stream=True
                ):
                    if kind == "delta":
                        delta = str(payload["text"])
                        parts.append(delta)
                        yield "delta", {"text": delta}
                    elif kind == "tool":
                        yield "tool", payload
    except UpstreamError as exc:
        failure = exc
    finally:
        # 正常结束、上游失败、客户端断开都走这里:已产出的部分必须落库
        # (finally 里只允许 await,不能再 yield——断开时 yield 会 RuntimeError)
        usage_service.unbind(usage_token)
        text = "".join(parts)
        if text:
            reply = await _persist_reply(factory, message, text)
        await usage_service.report(
            tenant_id=tenant_id, run_id=None, samples=collector.samples
        )
    if failure is not None:
        yield "error", {"code": failure.code, "message": failure.message}
        return
    yield "done", {"mode": outcome, "reply": reply}
    # 记忆抽取放到 done 之后:客户端先拿到完成帧,抽取慢不会拖住收尾;独立短事务,
    # 失败只 warning(客户端断开导致生成器关闭时跳过本轮抽取,不影响已落库回复)。
    if reply is not None:
        async with session_scope(factory) as extract_session:
            await _maybe_extract_memories(
                extract_session,
                llm,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                workspace_id=conversation_workspace_id,
                settings=settings,
            )
