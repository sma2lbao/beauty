"""改写路径:改写种类/非法改写错误/改写计划、计划构建与预检、流式改写。"""

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
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import triage
from agent_orchestrator.services.conversation_targets import (
    MessageTarget,
    require_conversation,
    resolve_message_target,
)
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.llm_binding import model_for_conversation
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace

from .send import _prepare_turn
from .stream import _stream_turn

if TYPE_CHECKING:  # 仅用于类型标注,避免 services 层与工具层的导入环。
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    #: 外部工具(MCP/skill_run)工厂:按账号现取,与 run 执行图共用
    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]
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
