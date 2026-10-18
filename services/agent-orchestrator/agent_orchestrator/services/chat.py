"""直接对话回复(§6.7):不建 run、不进执行图,直接用模型答一条。

与任务路径的分工:chat 只回答——不产生副作用、不产出交付物、不需要审批;
模型若判断"这其实是任务",就在回复里引导用户改用任务发送。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import MessageRepository
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import history
from agent_orchestrator.services.llm import LLMClient

CHAT_SYSTEM_PROMPT = (
    "你是对话助手。这条消息属于简单对话,不需要多步执行或调用工具:"
    "直接、简洁地回答(一般 2-5 句),不要输出执行计划、不要寒暄客套。"
    "如果它其实需要多步执行、调用工具或产出交付物,"
    "就用一句话说明并提示用户改用「任务」发送。"
)


async def prompt(session: AsyncSession, message: ChatMessage) -> list[dict[str, str]]:
    """直接对话的完整消息序列:系统提示 + 此前全部轮次 + 本轮。

    非流式与流式共用——两条路径的上下文口径因此不会漂。
    """
    rows = await MessageRepository(session).list_before(
        message.tenant_id,
        message.conversation_id,
        before=message.created_at,
        exclude_id=message.id,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.extend(history.to_prompt(rows))
    attachment_context = await attachments_service.context_for_message(
        session, message.tenant_id, message.id
    )
    content = message.content + attachment_context
    messages.append({"role": "user", "content": content})
    return messages


async def answer(session: AsyncSession, llm: LLMClient, message: ChatMessage) -> str:
    """给一条用户消息生成直接回复(一次拿全文,非流式路径)。"""
    reply = await llm.complete(await prompt(session, message))
    return reply.content
