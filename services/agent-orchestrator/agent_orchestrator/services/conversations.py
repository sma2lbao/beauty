"""conversation 编排业务逻辑:不 import FastAPI,可独立单测。

发一条消息先**分流**(§6.7,判别见 services/triage.py),再按路径落库:

- **chat(简单对话)**:直接由模型答一条,不建 run、不进执行图——不产生副作用、
  不需要审批;用户消息与 assistant 回复都持久化,成为后续的多轮上下文;
- **task(复杂任务)**:消息即 goal(或并入进行中 run 的追加指令队列 §6.5),
  新开一跑时**立即规划**——对话里 Agent 的第一响应就是计划。

"进行中"口径与对话页一致:completed/failed/blocked 之外的状态;blocked 需人工
resume,新任务消息应该另开一跑。首条消息自动定题;两条路径的消息都持久化,
构成多轮上下文(runs 建图时带 conversation_history,chat 带此前全部轮次)。
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
from agent_orchestrator.services import chat, triage
from agent_orchestrator.services import runs as runs_service
from agent_orchestrator.services.llm import LLMClient

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
    llm: LLMClient | None,
    tenant_id: str,
    conversation_id: str,
    content: str,
    *,
    mode: str = triage.AUTO,
) -> tuple[ChatMessage, Run | None, ChatMessage | None, str]:
    """发消息:先分流(§6.7),再按 chat / task 路径持久化。

    返回 (用户消息, run|None, assistant 直接回复|None, 处理方式);
    处理方式 ∈ {"chat", "run_created", "instruction_queued"}。
    """
    conversation = await require_conversation(session, tenant_id, conversation_id)
    messages = MessageRepository(session)
    if not await messages.has_any_message(tenant_id, conversation_id):
        # 首条消息自动定题
        conversation.title = title_from_message(content)

    # 先落用户消息:created_at 必须早于随后创建的 run / assistant 回复,
    # 多轮历史(排除消息自身或本 run 的消息)才不会把它重复算进上下文
    message = ChatMessage(
        tenant_id=tenant_id,
        conversation_id=conversation.id,
        role="user",
        content=content,
    )
    await messages.add(message)

    decided = await triage.decide(content, llm, mode=mode)
    # llm 为空时 decide 不会给出 chat(显式 chat 无模型会 409),这里的判空只为收窄类型
    if decided == triage.CHAT and llm is not None:
        reply = ChatMessage(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role="assistant",
            content=await chat.answer(session, llm, message),
        )
        await messages.add(reply)
        conversation.updated_at = datetime.now(UTC)
        return message, None, reply, triage.CHAT

    run = await active_run(session, tenant_id, conversation_id)
    if run is not None:
        # 进行中:并入追加指令(§6.5),消息挂到该 run
        await runs_service.queue_instruction(session, tenant_id, run.id, content)
        message.run_id = run.id
        outcome = "instruction_queued"
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
        outcome = "run_created"
        await runs_service.plan_run(session, graph, tenant_id, run.id)

    conversation.updated_at = datetime.now(UTC)
    return message, run, None, outcome
