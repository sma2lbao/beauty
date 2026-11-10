"""发消息的两条姿势:一次拿全文,或产出事件流(自 conversations.py 拆出)。

两者共用分流(§6.7)与上下文口径:先落用户消息与分流结论(chat 顺带读多轮
上下文),chat 再产回复——一次拿全文或逐帧流式;task 路径交给 _task_path
建 run 或并入追加指令队列(§6.5)。

重写(编辑重发 / 重新生成,interaction-closure §2.1)复用同一套分流与流式
实现:stream_rewrite 只多一段"校验 + 覆盖/删除最后一轮"的首段事务,之后与
stream_message 走同一个 _stream_turn。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from common.artifacts import ArtifactStore
from common.errors import AppError, ConflictError, NotFoundError
from database.session import session_scope
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.context.pipeline import ContextPipeline
from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import MessageRepository, RunRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import chat, post_commit, tool_confirmations, triage
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.conversation_targets import (
    MessageTarget,
    require_conversation,
    resolve_message_target,
)
from agent_orchestrator.services.conversations_context import (
    _maybe_extract_memories,
    _prepare_chat_context,
    _schedule_chat_extraction,
    _task_compaction,
)
from agent_orchestrator.services.conversations_persist import (
    _persist_assistant_message,
    _persist_reply,
    _persist_user_message,
    _task_path,
    _toolkit_for,
)
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient, UpstreamError
from agent_orchestrator.services.llm_binding import (
    bind_dialogue_llm,
    model_for_conversation,
)
from agent_orchestrator.services.skill_packages import SkillDefinition
from agent_orchestrator.services.skills import SkillRegistry
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


#: 重写时视为"已经定稿"的 run 状态;不在其中的一律当作还在跑(409)
REWRITE_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.BLOCKED.value,
    }
)

#: 被重写的消息角色:user=编辑重发,assistant=重新生成
RewriteKind = Literal["edit", "regenerate"]


class InvalidRewrite(AppError):
    """重写请求本身不合法(content 该给没给 / 不该给却给了)→ 422。"""

    status = 422


@dataclass(frozen=True, slots=True)
class RewritePlan:
    """一次重写的落库计划:校验阶段算出,事务里照着执行。"""

    conversation: Conversation
    user_message: ChatMessage
    content: str | None
    discard: list[ChatMessage]
    target: MessageTarget
    kind: RewriteKind


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


async def _rewrite_plan(
    session: AsyncSession,
    account_id: str,
    conversation_id: str,
    message_id: str,
    content: str | None,
    *,
    skills: SkillRegistry,
    settings: Settings | None,
) -> RewritePlan:
    """校验重写请求并算出落库计划(不写库,事务内可重复调用)。

    只允许改**最后一轮**:user 目标必须是最后一条 user 消息、其后最多一条
    assistant;assistant 目标必须是最后一条消息且前一条是 user。被丢弃的消息
    若挂着未终结的 run,说明这一轮还在跑,重写会与执行结果打架 → 409。
    """
    conversation = await require_conversation(session, account_id, conversation_id)
    messages = MessageRepository(session)
    target_message = await messages.get_for_account(account_id, message_id)
    if target_message is None or target_message.conversation_id != conversation_id:
        raise NotFoundError(
            "conversation.message_not_found",
            "消息不存在",
            details={"message_id": message_id},
        )
    rows = await messages.list_for_conversation(account_id, conversation_id)
    index = next((i for i, row in enumerate(rows) if row.id == message_id), -1)
    if index < 0:
        raise NotFoundError(
            "conversation.message_not_found",
            "消息不存在",
            details={"message_id": message_id},
        )
    not_latest = ConflictError(
        "conversation.message_not_latest", "只能重写最后一轮消息"
    )
    discard: list[ChatMessage]
    if target_message.role == "user":
        # 编辑重发:必须有新正文(1..2000 由 schema 卡上限,这里只挡空)
        if not content:
            raise InvalidRewrite(
                "conversation.content_required", "编辑重发必须提供新的消息内容"
            )
        last_user_index = max(
            (i for i, row in enumerate(rows) if row.role == "user"), default=-1
        )
        if index != last_user_index:
            raise not_latest
        discard = rows[index + 1 :]
        if len(discard) > 1 or (discard and discard[0].role != "assistant"):
            raise not_latest
        user_message = target_message
        kind: RewriteKind = "edit"
    elif target_message.role == "assistant":
        # 重新生成:不接受正文,复用前一条用户消息
        if content is not None:
            raise InvalidRewrite(
                "conversation.content_not_allowed", "重新生成不接受消息内容"
            )
        if index != len(rows) - 1 or index == 0 or rows[index - 1].role != "user":
            raise not_latest
        discard = [target_message]
        user_message = rows[index - 1]
        kind = "regenerate"
    else:
        raise not_latest
    runs = RunRepository(session)
    for discarded in discard:
        if discarded.run_id is None:
            continue
        run = await runs.get_for_account(account_id, discarded.run_id)
        if run is not None and run.status not in REWRITE_TERMINAL_RUN_STATUSES:
            raise ConflictError(
                "conversation.run_active",
                "该轮任务仍在执行,无法重写",
                details={"run_id": run.id, "status": run.status},
            )
    # 复用轮不继承 skill / 模型覆盖,只带上原消息显式引用的 MCP server
    target = await resolve_message_target(
        session,
        account_id,
        skills=skills,
        settings=settings,
        skill_id=None,
        mcp_server_ids=list(user_message.mcp_server_ids or []),
    )
    return RewritePlan(
        conversation=conversation,
        user_message=user_message,
        content=content,
        discard=discard,
        target=target,
        kind=kind,
    )


async def precheck_rewrite(
    factory: async_sessionmaker[AsyncSession],
    account_id: str,
    conversation_id: str,
    message_id: str,
    content: str | None,
    *,
    skills: SkillRegistry,
    settings: Settings | None,
) -> None:
    """开流前的只读校验:404/409/422 必须在 SSE 200 之前给出。

    与 stream_rewrite 复用同一份 _rewrite_plan,不会出现"开流前放行、
    开流后才发现不合法"的两套判定。
    """
    async with session_scope(factory) as session:
        await _rewrite_plan(
            session,
            account_id,
            conversation_id,
            message_id,
            content,
            skills=skills,
            settings=settings,
        )


async def _stream_turn(
    factory: async_sessionmaker[AsyncSession],
    llm: LLMClient | None,
    *,
    account_id: str,
    conversation: Conversation,
    message: ChatMessage,
    resolved_model: str | None,
    outcome: str,
    run: Run | None,
    prompt: list[dict[str, Any]],
    mcp_server_ids: list[str] | None,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ExtrasFactory | None,
    settings: Settings | None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """流式产出本轮增量(不占事务);发消息与重写共用第二段。

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
    if run is not None or llm is None:
        # 任务路径的进展由 run 自己的事件流呈现,这里不重复
        yield "done", {"mode": outcome, "reply": None}
        return
    conversation_id = conversation.id
    # 第二段:流式产出增量(不占事务);顺带采集本轮用量(P0-1,run_id 为空)
    collector = usage_service.UsageCollector()
    usage_token = usage_service.bind(collector)
    parts: list[str] = []
    failure: UpstreamError | None = None
    reply: ChatMessage | None = None
    try:
        # 对话内工具执行的来源上下文(stdio MCP 沙箱按对话复用);
        # 生成器被 aclose 时 with 会退出,reset contextvar,不跨请求泄漏
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
                        policy_session, account_id, conversation_id
                    )
                    hooks = await hooks_service.load_hooks(policy_session, account_id)
                # 写操作就地确认:流式路径不在长事务里,每条 pending 独立短事务落库
                confirmations = tool_confirmations.StreamConfirmationRecorder(
                    factory, account_id=account_id, conversation_id=conversation_id
                )
                async for kind, payload in chat.run_tool_loop(
                    llm,
                    prompt,
                    toolkit,
                    policies=policies,
                    stream=True,
                    confirmations=confirmations,
                    hooks=hooks,
                    hook_base={
                        "account_id": account_id,
                        "conversation_id": conversation_id,
                    },
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
            account_id=account_id, run_id=None, samples=collector.samples
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
                account_id=account_id,
                conversation_id=conversation_id,
                workspace_id=conversation.workspace_id,
                settings=settings,
            )


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


async def stream_rewrite(
    factory: async_sessionmaker[AsyncSession],
    graph: CompiledStateGraph,
    llm: LLMClient | None,
    account_id: str,
    conversation_id: str,
    message_id: str,
    content: str | None,
    *,
    skills: SkillRegistry,
    jobs_enabled: bool = False,
    toolkit_factory: ToolkitFactory | None = None,
    extras_factory: ExtrasFactory | None = None,
    store: ArtifactStore | None = None,
    workspace: Workspace | None = None,
    downloader: FileDownloader | None = None,
    max_download_files: int = 10,
    settings: Settings | None = None,
    context_pipeline: ContextPipeline | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """编辑重发 / 重新生成:覆盖或复用最后一轮,再走同一条流式流水线。

    - user 目标(编辑重发):新正文覆盖该消息,删除其后的消息(通常一条
      assistant);附件与 mcp_server_ids 保留;
    - assistant 目标(重新生成):删除该 assistant,复用前一条 user 的正文/
      附件/MCP 引用。

    复用轮按 mode=auto 重新分流,不继承 skill 选择与模型覆盖(走会话默认模型);
    帧序列与 /messages/stream 完全一致(meta → delta/tool → done/error)。
    校验 + 覆盖/删除在首段事务里完成,流式段不占事务(与发消息口径一致)。
    """
    run: Run | None = None
    prompt: list[dict[str, Any]] = []
    resolved_model: str | None = None
    async with session_scope(factory) as session:
        plan = await _rewrite_plan(
            session,
            account_id,
            conversation_id,
            message_id,
            content,
            skills=skills,
            settings=settings,
        )
        conversation = plan.conversation
        messages = MessageRepository(session)
        for discarded in plan.discard:
            await messages.delete(discarded)
        message = plan.user_message
        if plan.content is not None:
            message.content = plan.content
        conversation.updated_at = datetime.now(UTC)
        # 附件上下文按"复用后"的用户消息取:编辑不重传附件,仍能看到原文附件
        attachment_context = await attachments_service.context_for_message(
            session, account_id, message.id
        )
        # 复用轮同样看附件里有没有图:带图重发不该被判成任务(任务不注入图片)
        has_images = await attachments_service.message_has_images(
            session, account_id, message.id
        )
        # 不继承 model 覆盖:走会话默认模型(conversation.model 可能为空 → 网关默认)
        resolved_model = model_for_conversation(conversation)
        prompt, run, outcome = await _prepare_turn(
            session,
            graph,
            llm,
            account_id,
            conversation,
            message,
            message.content,
            attachment_context=attachment_context,
            resolved_model=resolved_model,
            has_images=has_images,
            mode=triage.AUTO,
            jobs_enabled=jobs_enabled,
            skill=plan.target.skill,
            mcp_server_ids=plan.target.mcp_server_ids,
            mcp_reference_note=plan.target.mcp_reference_note,
            store=store,
            workspace=workspace,
            downloader=downloader,
            max_download_files=max_download_files,
            settings=settings,
            context_pipeline=context_pipeline,
            model=None,
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
        mcp_server_ids=plan.target.mcp_server_ids,
        toolkit_factory=toolkit_factory,
        extras_factory=extras_factory,
        settings=settings,
    ):
        yield event
