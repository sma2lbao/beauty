"""直接对话回复(§6.7):不建 run、不进执行图,直接用模型答一条。

与任务路径的分工:chat 只回答——不产生副作用、不产出交付物、不需要审批;
模型若判断"这其实是任务",就在回复里引导用户改用任务发送。

对话内 function tools(只读):查资料类工具(邮件/文档/日历/知识库检索)
允许在 chat 里直接调用——问"我最近一封邮件是谁发的"不该被迫建 run;
写操作工具一律拒绝执行,回喂错误并引导改用「任务」(任务路径有确认闸 §6.2)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import MessageRepository
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import history
from agent_orchestrator.services.llm import LLMClient

MAX_CHAT_TOOL_ROUNDS = 3  # 对话内 function-calling 轮次上限

CHAT_SYSTEM_PROMPT = (
    "你是对话助手。可以调用提供的只读工具查询信息(如邮件、文档、日历、"
    "知识库),拿到结果后直接、简洁地回答(一般 2-5 句),不要输出执行计划、"
    "不要寒暄客套。写操作(如发邮件)请提示用户改用「任务」发送——"
    "任务路径有人工确认。如果这个问题需要多步执行或产出交付物,"
    "也用一句话说明并提示用户改用「任务」发送。"
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


def _tool_error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _assistant_tool_message(reply) -> dict[str, Any]:
    """模型的工具请求回填进消息历史(OpenAI 嵌套形状,与 execute 节点一致)。"""
    return {
        "role": "assistant",
        "content": reply.content or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in reply.tool_calls
        ],
    }


def _tool_message(call_id: str, output: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": output}


async def run_tool_loop(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    toolkit,
) -> AsyncIterator[tuple[str, dict[str, Any] | str]]:
    """对话内 function-calling 循环(只读 + 受控写,§6.7)。

    产出 ("tool", {name, arguments, ok}) 事件供流式层转发;最终一轮产出
    ("content", 正文)。只读与受控写工具自动执行;**危险操作(DANGER)不执行**,
    回喂错误文本引导走任务路径(对话没有审批流)。
    轮次达上限仍无正文时,用最后收到的模型输出兜底(可能为空)。
    """
    schemas = toolkit.schemas()
    last_text = ""
    for _ in range(MAX_CHAT_TOOL_ROUNDS):
        reply = await llm.complete(messages, tools=schemas)
        last_text = reply.content or last_text
        if not reply.tool_calls:
            yield "content", reply.content
            return
        messages.append(_assistant_tool_message(reply))
        for call in reply.tool_calls:
            if toolkit.requires_confirmation(call.name):
                # 危险操作在 chat 中不执行:回喂错误让模型引导用户走任务
                output = _tool_error(
                    "危险操作不允许在对话中执行,请提示用户改用「任务」发送"
                )
            else:
                output = await toolkit.execute(call.name, call.arguments)
            messages.append(_tool_message(call.id, output))
            yield "tool", {
                "name": call.name,
                "arguments": call.arguments,
                "ok": not output.startswith('{"error"'),
            }
    yield "content", last_text


async def answer(
    session: AsyncSession,
    llm: LLMClient,
    message: ChatMessage,
    toolkit=None,
) -> str:
    """给一条用户消息生成直接回复(一次拿全文,非流式路径)。

    toolkit 为空(未配置工具)时行为与原先完全一致:单轮直答。
    """
    messages = await prompt(session, message)
    if toolkit is None or len(toolkit) == 0:
        reply = await llm.complete(messages)
        return reply.content
    final = ""
    async for kind, payload in run_tool_loop(llm, messages, toolkit):
        if kind == "content":
            final = str(payload)
    return final
