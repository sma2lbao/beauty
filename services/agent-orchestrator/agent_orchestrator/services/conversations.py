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

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from common.artifacts import ArtifactStore
from common.errors import AppError, NotFoundError
from common.logging import get_logger
from database.session import session_scope
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    ConversationRepository,
    MessageRepository,
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunCreate, RunStatus
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import chat, triage
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services import usage as usage_service
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

DEFAULT_TITLE = "新对话"
logger = get_logger(__name__)
MAX_TITLE = 40

#: 会话内不再接收消息的 run 状态(completed/failed 是终态;
#: blocked 需人工 resume,新消息另开一跑)
INACTIVE_STATUSES = frozenset(
    {
        RunStatus.COMPLETED.value,
        RunStatus.FAILED.value,
        RunStatus.BLOCKED.value,
    }
)


def title_from_message(content: str) -> str:
    """用消息首行做标题(过长截断;与前端旧的 titleFromMessage 语义一致)。"""
    first_line = content.strip().split("\n")[0].strip()
    title = first_line or DEFAULT_TITLE
    return f"{title[:MAX_TITLE]}…" if len(title) > MAX_TITLE else title


async def _default_workspace_id(
    workspace: Workspace | None, tenant_id: str, *, warn: bool = True
) -> str | None:
    """租户默认工作区 id;客户端未启用/取不到/上游故障 → None(不阻断对话)。

    warn=False 用于列表接口的懒回填:未启用工作区是合法部署形态,不必每次
    拉列表都刷警告;建对话时(WARN)则要留下"归属缺失"的唯一线索。
    """
    if workspace is None:
        if warn:
            logger.warning(
                "workspace client disabled, conversation has no workspace",
                tenant_id=tenant_id,
            )
        return None
    try:
        default = await workspace.default_workspace(tenant_id)
    except Exception as exc:  # noqa: BLE001 - 工作区故障只降级,不阻断建对话
        if warn:
            logger.warning(
                "default workspace resolution failed",
                tenant_id=tenant_id,
                error=f"{type(exc).__name__}: {exc}",
            )
        return None
    if not default:
        if warn:
            logger.warning(
                "workspace service returned no default workspace",
                tenant_id=tenant_id,
            )
        return None
    resolved = default.get("id")
    return str(resolved) if resolved else None


async def resolve_workspace_id(
    workspace: Workspace | None,
    tenant_id: str,
    workspace_id: str | None = None,
) -> str | None:
    """确定对话所属工作区:显式 id 先校验,未给则取租户默认工作区。

    - 显式 id:经 workspace 客户端校验存在性,不存在 → 404 workspace.not_found;
      客户端不可达 → 502 workspace.upstream_error(显式指定就不静默改归属);
    - 显式 id + 客户端未启用:无法校验存在性,按原样接受并记 warning——
      "工作区集成没开"不应该让建对话失败;
    - 未给 id:落租户默认工作区;客户端未启用/上游故障 → None + warning 降级,
      对话照常建(归属缺失由列表接口的懒回填兜底)。
    """
    if workspace_id:
        if workspace is None:
            logger.warning(
                "workspace client disabled, workspace_id accepted unverified",
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
            return workspace_id
        found = await workspace.get_workspace(tenant_id, workspace_id)
        if found is None:
            raise NotFoundError("workspace.not_found", "工作区不存在")
        return workspace_id
    return await _default_workspace_id(workspace, tenant_id)


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


def encode_cursor(conversation_id: str) -> str:
    """把分页锚点编码成不透明游标(base64url,无填充)。"""
    raw = base64.urlsafe_b64encode(conversation_id.encode()).decode()
    return raw.rstrip("=")


def decode_cursor(cursor: str) -> str:
    """解码游标;非法游标给 400(不泄漏内部格式)。"""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except Exception as exc:  # noqa: BLE001 - 任何解码失败都视为坏游标
        raise AppError("conversation.bad_cursor", "分页游标无效") from exc


async def _backfill_legacy_workspace(
    repo: ConversationRepository, tenant_id: str, workspace_id: str
) -> None:
    """把归属为空的存量会话补到该工作区;失败只记 warning(兼容性补丁)。"""
    try:
        filled = await repo.backfill_workspace_id(tenant_id, workspace_id)
    except Exception as exc:  # noqa: BLE001 - 回填失败不影响列表
        logger.warning(
            "conversation workspace backfill failed",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    if filled:
        logger.info(
            "conversations backfilled with default workspace",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            count=filled,
        )


async def list_conversations(
    session: AsyncSession,
    tenant_id: str,
    limit: int = 50,
    *,
    q: str | None = None,
    archived: bool = False,
    cursor: str | None = None,
    workspace_id: str | None = None,
    workspace: Workspace | None = None,
) -> tuple[list[Conversation], str | None]:
    """租户内最近活跃在前的对话列表(消息会顶 updated_at)。

    keyset 分页:多取一条判断 has_more,用最后一条 id 作下一页游标。

    存量空归属的回填(一次 UPDATE,失败只记 warning,不拖挂列表):旧会话按
    "属于租户默认工作区"处理。未给 workspace_id 时总是回填;给了过滤时只在
    过滤的就是默认工作区时回填——默认工作区首屏因此能看到历史会话,而按其他
    工作区过滤时既不动归属、也不会把会话泄漏到别的过滤结果里。
    """
    repo = ConversationRepository(session)
    # 空串按“未给”处理:?workspace_id= 不应变成“过滤一个空工作区”
    workspace_id = workspace_id or None
    # 尽力解析默认工作区:失败只是 None(仅影响回填,不影响过滤结果)
    default_id = await _default_workspace_id(workspace, tenant_id, warn=False)
    if default_id is not None and (workspace_id is None or workspace_id == default_id):
        await _backfill_legacy_workspace(repo, tenant_id, default_id)
    before_id = decode_cursor(cursor) if cursor else None
    rows = await repo.list_recent(
        tenant_id,
        limit + 1,
        q=q,
        archived=archived,
        before_id=before_id,
        workspace_id=workspace_id,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return items, next_cursor


async def require_conversation(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> Conversation:
    """取租户内未删除的对话;不存在/跨租户/已删 → NotFoundError(404)。"""
    return await ConversationRepository(session).require_active_for_tenant(
        tenant_id, conversation_id
    )


async def rename_conversation(
    session: AsyncSession, tenant_id: str, conversation_id: str, title: str
) -> Conversation:
    """重命名会话(标题来自用户编辑,保留首尾空白清理)。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    cleaned = title.strip()
    if not cleaned:
        raise AppError("conversation.invalid_title", "会话标题不能为空")
    conversation.title = cleaned
    conversation.updated_at = datetime.now(UTC)
    return conversation


async def set_archived(
    session: AsyncSession, tenant_id: str, conversation_id: str, archived: bool
) -> Conversation:
    """归档 / 取消归档。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    conversation.archived_at = datetime.now(UTC) if archived else None
    conversation.updated_at = datetime.now(UTC)
    return conversation


async def delete_conversation(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> None:
    """软删除:保留审计数据,列表/详情/消息随即按 404 处理。"""
    conversation = await require_conversation(session, tenant_id, conversation_id)
    conversation.deleted_at = datetime.now(UTC)
    conversation.updated_at = datetime.now(UTC)


async def active_run(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> Run | None:
    """会话当前进行中的 run:最新一跑仍在进行 → 它;否则 None。

    只看最新一跑:新 run 只在上一跑进入 inactive 状态后才会创建,
    所以"最新且进行中"即"当前对话正在执行的任务"。
    """
    runs = await RunRepository(session).list_for_conversation(
        tenant_id, conversation_id, limit=1
    )
    if not runs:
        return None
    return None if runs[0].status in INACTIVE_STATUSES else runs[0]


async def list_messages(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> list[ChatMessage]:
    """对话消息回放:按时间升序(user/assistant 混排)。"""
    await require_conversation(session, tenant_id, conversation_id)
    return await MessageRepository(session).list_for_conversation(
        tenant_id, conversation_id
    )


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
                mcp_server_ids=tuple(mcp_server_ids or ()),
            )
        ):
            toolkit = await _toolkit_for(toolkit_factory, extras_factory, tenant_id)
            policies = await approval_service.load_policies(session, tenant_id)
            answer = await chat.answer(
                session,
                llm,
                message,
                toolkit,
                reference_note=mcp_reference_note,
                policies=policies,
            )
        reply = await _persist_assistant_message(session, message, answer)
        return message, None, reply, triage.CHAT
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
    )
    return message, run, None, outcome


async def _persist_reply(
    factory: async_sessionmaker[AsyncSession], message: ChatMessage, text: str
) -> ChatMessage:
    """另一段短事务:落 assistant 回复(流式路径用)。"""
    async with session_scope(factory) as session:
        return await _persist_assistant_message(session, message, text)


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
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """流式发消息:yield (事件名, 载荷)。

    - meta:分流结论(含用户消息,chat 时 run 为空);
    - delta:正文增量(仅 chat);
    - tool:chat 内只读工具调用(对话内 function-calling,§6.8);
    - done:收尾(chat 带落库后的 assistant 回复);
    - error:失败(流已按 200 开头,只能用帧报错)。

    客户端中途断开时,已产出的增量仍会落库(不丢半截回复);全部失败则不留
    assistant 消息,由对话页展示错误。
    """
    # 第一段事务:用户消息 + 分流结论(chat 顺带读多轮上下文;task 建 run/排队)
    run: Run | None = None
    prompt: list[dict[str, str]] = []
    async with session_scope(factory) as session:
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
        if decided == triage.CHAT and llm is not None:
            prompt = await chat.prompt(
                session, message, reference_note=mcp_reference_note
            )
            outcome = triage.CHAT
        else:
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
                mcp_server_ids=tuple(mcp_server_ids or ()),
            )
        ):
            toolkit = await _toolkit_for(toolkit_factory, extras_factory, tenant_id)
            if toolkit is None or len(toolkit) == 0:
                # 无工具:保持原路径——真正的流式增量(边产出边显示)
                async for delta in llm.stream(prompt):
                    parts.append(delta)
                    yield "delta", {"text": delta}
            else:
                # 对话内 function-calling:complete 轮次 + 工具事件;
                # 最终正文一次性按字切分补发 delta(无工具轮时退化为一段输出)
                final = ""
                # 第一段事务已在 yield meta 前结束,此处必须开新事务:
                # 复用已 close 的 session 会隐式 autobegin 再签出连接,无人归还
                async with session_scope(factory) as policy_session:
                    policies = await approval_service.load_policies(
                        policy_session, tenant_id
                    )
                async for kind, payload in chat.run_tool_loop(
                    llm, prompt, toolkit, policies=policies
                ):
                    if kind == "tool":
                        yield "tool", payload
                    elif kind == "content":
                        final = str(payload)
                text = final
                for i in range(len(text)):
                    delta = text[i]
                    parts.append(delta)
                    yield "delta", {"text": delta}
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
