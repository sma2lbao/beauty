"""conversation 编排业务逻辑:不 import FastAPI,可独立单测。

消息与任务(§6)的联动语义:

- 没有进行中的 run:消息即 goal,新开一跑并**立即规划**——对话里 Agent
  的第一响应就是计划,审批/确认按钮就地出现在消息流里;
- 有进行中的 run:消息并入追加指令队列(§6.5),不新开一跑;
- "进行中"口径与对话页一致:completed/failed/blocked 之外的状态;
  blocked 需人工 resume,新消息应该另开一跑,而不是挂在旧图上;
- 首条消息自动定题;用户与 assistant 消息都持久化,构成多轮上下文
  (runs 服务在建图时带 conversation_history)。
"""

from __future__ import annotations

from datetime import UTC, datetime

from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import (
    ConversationRepository,
    MessageRepository,
    RunRepository,
)
from agent_orchestrator.schemas.runs import RunCreate, RunStatus
from agent_orchestrator.services import runs as runs_service

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


async def list_conversations(
    session: AsyncSession, tenant_id: str, limit: int = 50
) -> list[Conversation]:
    """租户内最近活跃在前的对话列表(消息会顶 updated_at)。"""
    return await ConversationRepository(session).list_recent(tenant_id, limit)


async def require_conversation(
    session: AsyncSession, tenant_id: str, conversation_id: str
) -> Conversation:
    """取租户内对话;不存在(或跨租户)→ NotFoundError(404)。"""
    return await ConversationRepository(session).require_for_tenant(
        tenant_id, conversation_id
    )


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


async def post_message(
    session: AsyncSession,
    graph: CompiledStateGraph,
    tenant_id: str,
    conversation_id: str,
    content: str,
) -> tuple[ChatMessage, Run, str]:
    """发消息:持久化用户消息,并按上下文并入当前 run 或新开一跑。

    返回 (message, run, mode),mode ∈ {"run_created", "instruction_queued"}。
    """
    conversation = await require_conversation(session, tenant_id, conversation_id)
    messages = MessageRepository(session)
    if not await messages.has_any_message(tenant_id, conversation_id):
        # 首条消息自动定题
        conversation.title = title_from_message(content)

    # 先落用户消息:created_at 必须早于随后创建的 run,
    # 多轮历史(排除本 run 自己的消息)才不会把它重复算进上下文
    message = ChatMessage(
        tenant_id=tenant_id,
        conversation_id=conversation.id,
        role="user",
        content=content,
    )
    await messages.add(message)

    run = await active_run(session, tenant_id, conversation_id)
    if run is not None:
        # 进行中:并入追加指令(§6.5),消息挂到该 run
        await runs_service.queue_instruction(session, tenant_id, run.id, content)
        message.run_id = run.id
        mode = "instruction_queued"
    else:
        # 没有进行中的任务:消息即 goal,新开一跑(挂到本会话)并立即规划
        run = await runs_service.create_run(
            session,
            tenant_id,
            RunCreate(
                title=title_from_message(content),
                goal=content,
                acceptance_criteria=[],
                conversation_id=conversation.id,
            ),
        )
        message.run_id = run.id
        mode = "run_created"
        await runs_service.plan_run(session, graph, tenant_id, run.id)

    conversation.updated_at = datetime.now(UTC)
    return message, run, mode
