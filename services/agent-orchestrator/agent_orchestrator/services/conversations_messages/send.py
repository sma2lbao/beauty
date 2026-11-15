"""发消息路径:非流式 post_message(含用量上报)与准备阶段 _prepare_turn。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from common.artifacts import ArtifactStore
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import chat, post_commit, tool_confirmations, triage
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.conversation_targets import require_conversation
from agent_orchestrator.services.conversations_context import (
    _maybe_extract_memories,
    _prepare_chat_context,
    _schedule_chat_extraction,
    _task_compaction,
)
from agent_orchestrator.services.conversations_persist import (
    _persist_assistant_message,
    _persist_user_message,
    _task_path,
    _toolkit_for,
)
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.llm_binding import (
    bind_dialogue_llm,
    model_for_conversation,
)
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.workspace import Workspace

if TYPE_CHECKING:  # 仅用于类型标注,避免 services 层与工具层的导入环。
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    #: 外部工具(MCP/skill_run)工厂:按账号现取,与 run 执行图共用
    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]
def _schedule_usage_report(
    session: AsyncSession, account_id: str, samples: list[usage_service.UsageSample]
) -> bool:
    """非流式对话的用量记账(P0-2):优先推迟到事务提交之后。

    为什么不在请求事务里直接 report:用量走 outbox 时会另开一个写事务
    (SQLite 单写锁),与未提交的请求事务抢锁;提交后再报也能保证"已经烧掉的
    token"随响应成功一起记账。没有提交后回调能力(服务层直调/单测)时返回
    False,由调用方退回同步 report 保持旧语义。
    """
    if not samples or post_commit.runtime_of(session) is None:
        return False

    async def _report() -> None:
        await usage_service.report(account_id=account_id, run_id=None, samples=samples)

    post_commit.register_after_commit(session, _report)
    return True


async def post_message(
    session: AsyncSession,
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
) -> tuple[ChatMessage, Run | None, ChatMessage | None, str]:
    """发消息(一次拿全文):先分流(§6.7),再按 chat / task 路径持久化。

    返回 (用户消息, run|None, assistant 直接回复|None, 处理方式);
    处理方式 ∈ {"chat", "run_created", "instruction_queued"}。

    chat 路径携带只读工具(toolkit_factory + extras_factory):对话内
    function-calling(§6.8);外部工具(MCP)里 requires_confirmation 的会被
    chat.run_tool_loop 拒绝并提示改用「任务」(对话没有审批流,§6.7)。

    context_pipeline 非空时透传给建 run 的路径:起图那一刻按策略快照上下文
    (AGENTS.md/技能卡片/记忆),chat 路径用不到(它自己拼多轮上下文)。

    model 是本次消息的模型覆盖(P1-G):优先级 message > conversation > 网关默认;
    chat 分支的模型调用(triage/压缩/直答)与新建 run 的快照都用它。
    """
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
    with bind_dialogue_llm(
        account_id=account_id,
        conversation_id=conversation_id,
        model=resolved_model,
    ):
        decided = await triage.decide(content, llm, mode=mode, has_images=has_images)
    # llm 为空时 decide 不会给出 chat(显式 chat 无模型会 409),这里判空只为收窄类型
    if decided == triage.CHAT and llm is not None:
        # 工具 handler 执行时读得到对话上下文(stdio MCP 沙箱按对话复用);
        # mcp_server_ids 让外部工具工厂只装配被引用的 server
        with (
            bind_dialogue_llm(
                account_id=account_id,
                conversation_id=conversation_id,
                model=resolved_model,
            ),
            bind_tool_context(
                ToolContext(
                    account_id=account_id,
                    conversation_id=conversation_id,
                    workspace_id=conversation.workspace_id,
                    mcp_server_ids=tuple(mcp_server_ids or ()),
                )
            ),
        ):
            toolkit = await _toolkit_for(
                toolkit_factory, extras_factory, account_id, chat=True
            )
            policies = await approval_service.load_policies(
                session, account_id, conversation_id
            )
            # P1-B:hook 快照与 account/conversation 请求体上下文
            hooks = await hooks_service.load_hooks(session, account_id)
            hook_base = {
                "account_id": account_id,
                "conversation_id": conversation_id,
            }
            # 写操作就地确认:非流式路径与请求同事务落 pending(提交时一起生效)
            confirmations = tool_confirmations.ChatConfirmationRecorder(
                session, account_id=account_id, conversation_id=conversation_id
            )
            context = await _prepare_chat_context(
                session, llm, message, conversation, settings
            )
            # P0-2 直答也计入用量(口径与流式路径一致:只记正式回复,
            # run_id 为空;triage/上下文准备不重复计)
            collector = usage_service.UsageCollector()
            usage_token = usage_service.bind(collector)
            try:
                answer = await chat.answer(
                    session,
                    llm,
                    message,
                    toolkit,
                    reference_note=mcp_reference_note,
                    policies=policies,
                    context=context,
                    settings=settings,
                    confirmations=confirmations,
                    hooks=hooks,
                    hook_base=hook_base,
                )
            finally:
                usage_service.unbind(usage_token)
        reply = await _persist_assistant_message(session, message, answer)
        if not _schedule_usage_report(session, account_id, collector.samples):
            await usage_service.report(
                account_id=account_id, run_id=None, samples=collector.samples
            )
        # 有会话边界时推迟到提交后抽取(响应不等二次模型调用);服务层直调
        # (无 runtime 绑定)保持同步语义,行为与之前一致
        if not _schedule_chat_extraction(
            session,
            llm,
            account_id=account_id,
            conversation_id=conversation_id,
            workspace_id=conversation.workspace_id,
            settings=settings,
        ):
            await _maybe_extract_memories(
                session,
                llm,
                account_id=account_id,
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
        context_pipeline=context_pipeline,
        model=model,
    )
    return message, run, None, outcome


async def _prepare_turn(
    session: AsyncSession,
    graph: CompiledStateGraph,
    llm: LLMClient | None,
    account_id: str,
    conversation: Conversation,
    message: ChatMessage,
    content: str,
    *,
    attachment_context: str,
    resolved_model: str | None,
    has_images: bool = False,
    mode: str = triage.AUTO,
    jobs_enabled: bool = False,
    skill: SkillDefinition | None = None,
    mcp_server_ids: list[str] | None = None,
    mcp_reference_note: str = "",
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings | None = None,
    context_pipeline: ContextPipeline | None = None,
    model: str | None = None,
) -> tuple[list[dict[str, Any]], Run | None, str]:
    """分流并准备本轮:chat 拼好提示词,task 建 run 或并入追加指令。

    发消息与重写共用这一层,保证两条入口的分流/上下文/建 run 口径只有一份。
    返回 (prompt, run|None, 处理方式);task 路径 prompt 为空。

    has_images 表示本轮用户消息带图片:任务路径不注入图片,分流据此直答。
    """
    with bind_dialogue_llm(
        account_id=account_id,
        conversation_id=conversation.id,
        model=resolved_model,
    ):
        decided = await triage.decide(content, llm, mode=mode, has_images=has_images)
    # llm 为空时 decide 不会给出 chat(显式 chat 无模型会 409),这里判空只为收窄类型
    if decided == triage.CHAT and llm is not None:
        context = await _prepare_chat_context(
            session, llm, message, conversation, settings
        )
        prompt = await chat.prompt(
            session,
            message,
            reference_note=mcp_reference_note,
            context=context,
            settings=settings,
        )
        return prompt, None, triage.CHAT
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
        context_pipeline=context_pipeline,
        model=model,
    )
    return [], run, outcome
