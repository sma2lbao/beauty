"""直接对话回复(§6.7):不建 run、不进执行图,直接用模型答一条。

与任务路径的分工:chat 只回答——不产生副作用、不产出交付物、不需要审批;
模型若判断"这其实是任务",就在回复里引导用户改用任务发送。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import MessageRepository
from agent_orchestrator.services import history
from agent_orchestrator.services.llm import LLMClient

CHAT_SYSTEM_PROMPT = (
    "你是对话助手。这条消息属于简单对话,不需要多步执行或调用工具:"
    "直接、简洁地回答(一般 2-5 句),不要输出执行计划、不要寒暄客套。"
    "如果它其实需要多步执行、调用工具或产出交付物,"
    "就用一句话说明并提示用户改用「任务」发送。"
)


async def answer(session: AsyncSession, llm: LLMClient, message: ChatMessage) -> str:
    """给一条用户消息生成直接回复(带上它之前的全部对话轮次)。"""
    rows = await MessageRepository(session).list_before(
        message.tenant_id,
        message.conversation_id,
        before=message.created_at,
        exclude_id=message.id,
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.extend(history.to_prompt(rows))
    messages.append({"role": "user", "content": message.content})
    reply = await llm.complete(messages)
    return reply.content
