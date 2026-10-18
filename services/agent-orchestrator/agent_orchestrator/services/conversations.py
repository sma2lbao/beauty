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
from typing import Any

from common.errors import AppError
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
from agent_orchestrator.schemas.skills import SkillManifest
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import chat, triage
from agent_orchestrator.services import jobs as jobs_service
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.llm import LLMClient, UpstreamError

DEFAULT_TITLE = "新对话"
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


async def create_conversation(
    session: AsyncSession, tenant_id: str, title: str | None = None
) -> Conversation:
    conversation = Conversation(tenant_id=tenant_id, title=title or DEFAULT_TITLE)
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


async def list_conversations(
    session: AsyncSession,
    tenant_id: str,
    limit: int = 50,
    *,
    q: str | None = None,
    archived: bool = False,
    cursor: str | None = None,
) -> tuple[list[Conversation], str | None]:
    """租户内最近活跃在前的对话列表(消息会顶 updated_at)。

    keyset 分页:多取一条判断 has_more,用最后一条 id 作下一页游标。
    """
    before_id = decode_cursor(cursor) if cursor else None
    rows = await ConversationRepository(session).list_recent(
        tenant_id,
        limit + 1,
        q=q,
        archived=archived,
        before_id=before_id,
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
    session: AsyncSession, conversation: Conversation, content: str
) -> ChatMessage:
    """落用户消息(首条顺带定题)并顶起会话活跃时间。

    created_at 必须早于随后创建的 run / assistant 回复——多轮历史靠它排序。
    """
    messages = MessageRepository(session)
    if not await messages.has_any_message(conversation.tenant_id, conversation.id):
        conversation.title = title_from_message(content)
    message = ChatMessage(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        role="user",
        content=content,
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
    skill: SkillManifest | None = None,
) -> tuple[Run, str]:
    """任务路径:并入进行中的 run 的追加指令,或新开一跑并立即规划。"""
    run = await active_run(session, conversation.tenant_id, conversation.id)
    if run is not None:
        await runs_service.queue_instruction(
            session, conversation.tenant_id, run.id, content
        )
        message.run_id = run.id
        return run, "instruction_queued"
    run = await runs_service.create_run(
        session,
        conversation.tenant_id,
        RunCreate(
            title=title_from_message(content),
            goal=content,
            acceptance_criteria=[],
            conversation_id=conversation.id,
            skill_id=skill.id if skill else None,
        ),
        skill=skill,
    )
    message.run_id = run.id
    if jobs_enabled:
        await jobs_service.enqueue(
            session, conversation.tenant_id, "plan", run.id
        )
    else:
        await runs_service.plan_run(session, graph, conversation.tenant_id, run.id)
    return run, "run_created"


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
    skill: SkillManifest | None = None,
) -> tuple[ChatMessage, Run | None, ChatMessage | None, str]:
    """发消息(一次拿全文):先分流(§6.7),再按 chat / task 路径持久化。

    返回 (用户消息, run|None, assistant 直接回复|None, 处理方式);
    处理方式 ∈ {"chat", "run_created", "instruction_queued"}。
    """
    conversation = await require_conversation(session, tenant_id, conversation_id)
    message = await _persist_user_message(session, conversation, content)
    if attachment_ids:
        await attachments_service.link_attachments(
            session,
            tenant_id,
            conversation_id,
            message,
            attachment_ids,
            max_count=5,
        )
    decided = await triage.decide(content, llm, mode=mode)
    # llm 为空时 decide 不会给出 chat(显式 chat 无模型会 409),这里判空只为收窄类型
    if decided == triage.CHAT and llm is not None:
        reply = await _persist_assistant_message(
            session, message, await chat.answer(session, llm, message)
        )
        return message, None, reply, triage.CHAT
    run, outcome = await _task_path(
        session,
        graph,
        conversation,
        message,
        content,
        jobs_enabled=jobs_enabled,
        skill=skill,
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
    skill: SkillManifest | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """流式发消息:yield (事件名, 载荷)。

    - meta:分流结论(含用户消息,chat 时 run 为空);
    - delta:正文增量(仅 chat);
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
        message = await _persist_user_message(session, conversation, content)
        if attachment_ids:
            await attachments_service.link_attachments(
                session,
                tenant_id,
                conversation_id,
                message,
                attachment_ids,
                max_count=5,
            )
        decided = await triage.decide(content, llm, mode=mode)
        if decided == triage.CHAT and llm is not None:
            prompt = await chat.prompt(session, message)
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
            )
    yield "meta", {"mode": outcome, "message": message, "run": run}
    if run is not None or llm is None:
        # 任务路径的进展由 run 自己的事件流呈现,这里不重复
        yield "done", {"mode": outcome, "reply": None}
        return

    # 第二段:流式产出增量(不占事务)
    parts: list[str] = []
    failure: UpstreamError | None = None
    reply: ChatMessage | None = None
    try:
        async for delta in llm.stream(prompt):
            parts.append(delta)
            yield "delta", {"text": delta}
    except UpstreamError as exc:
        failure = exc
    finally:
        # 正常结束、上游失败、客户端断开都走这里:已产出的部分必须落库
        # (finally 里只允许 await,不能再 yield——断开时 yield 会 RuntimeError)
        text = "".join(parts)
        if text:
            reply = await _persist_reply(factory, message, text)
    if failure is not None:
        yield "error", {"code": failure.code, "message": failure.message}
        return
    yield "done", {"mode": outcome, "reply": reply}
