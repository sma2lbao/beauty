"""直接对话回复(§6.7):不建 run、不进执行图,直接用模型答一条。

与任务路径的分工:chat 只回答——不产生副作用、不产出交付物、不需要审批;
模型若判断"这其实是任务",就在回复里引导用户改用任务发送。

对话内 function tools(只读):查资料类工具(文档/知识库/代码读取)
允许在 chat 里直接调用——问"知识库里怎么写的"不该被迫建 run;
写操作工具一律拒绝执行,回喂错误并引导改用「任务」(任务路径有确认闸 §6.2)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph.tool_results import is_tool_error
from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import MessageRepository
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import history
from agent_orchestrator.services.llm import LLMClient

MAX_CHAT_TOOL_ROUNDS = 5  # 对话内 function-calling 轮次上限

CHAT_SYSTEM_PROMPT = (
    "你是对话助手。可以调用提供的只读工具查询信息(如文档、"
    "知识库),拿到结果后直接、简洁地回答(一般 2-5 句),不要输出执行计划、"
    "不要寒暄客套。写操作请提示用户改用「任务」发送——"
    "任务路径有人工确认。如果这个问题需要多步执行或产出交付物,"
    "也用一句话说明并提示用户改用「任务」发送。"
)


async def prompt(
    session: AsyncSession, message: ChatMessage, *, reference_note: str = ""
) -> list[dict[str, str]]:
    """直接对话的完整消息序列:系统提示 + 此前全部轮次 + 本轮。

    非流式与流式共用——两条路径的上下文口径因此不会漂。
    reference_note 是本次消息显式引用 MCP server 的约束片段(空则不追加)。
    """
    rows = await MessageRepository(session).list_before(
        message.tenant_id,
        message.conversation_id,
        before=message.created_at,
        exclude_id=message.id,
    )
    system_prompt = CHAT_SYSTEM_PROMPT
    if reference_note:
        system_prompt = f"{system_prompt}\n\n{reference_note}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
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
    *,
    policies: list[dict[str, str]] | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any] | str]]:
    """对话内 function-calling 循环(只读 + 受控写,§6.7)。

    产出 ("tool", {name, arguments, ok}) 事件供流式层转发;最终一轮产出
    ("content", 正文)。只读与受控写工具自动执行;**危险操作(DANGER)不执行**,
    回喂错误文本引导走任务路径(对话没有审批流);租户策略 ``deny`` 的工具同样不执行。
    轮次达上限仍在要工具时,再要一次"不带工具"的收束调用,避免已执行的
    工具白跑、正文为空;真拿不到正文再用最后一轮输出兜底。
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
            decision = approval_service.decide(
                policies, call.name, toolkit.risk(call.name)
            )
            if decision is approval_service.ApprovalAction.DENY:
                output = _tool_error(f"工具 {call.name} 被租户审批策略禁止使用")
            elif decision is approval_service.ApprovalAction.CONFIRM:
                # 需要人工确认的动作在 chat 中不执行:回喂错误让模型引导走任务
                output = _tool_error(
                    "危险操作不允许在对话中执行,请提示用户改用「任务」发送"
                )
            else:
                output = await toolkit.execute(call.name, call.arguments)
            messages.append(_tool_message(call.id, output))
            yield (
                "tool",
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "ok": not is_tool_error(output),
                },
            )
    # 轮次用尽仍在要工具:补一次不带工具的收束,让已执行的结果落到正文里
    reply = await llm.complete(messages)
    last_text = reply.content or last_text
    yield "content", last_text


async def answer(
    session: AsyncSession,
    llm: LLMClient,
    message: ChatMessage,
    toolkit=None,
    *,
    reference_note: str = "",
    policies: list[dict[str, str]] | None = None,
) -> str:
    """给一条用户消息生成直接回复(一次拿全文,非流式路径)。

    toolkit 为空(未配置工具)时行为与原先完全一致:单轮直答。
    """
    messages = await prompt(session, message, reference_note=reference_note)
    if toolkit is None or len(toolkit) == 0:
        reply = await llm.complete(messages)
        return reply.content
    final = ""
    async for kind, payload in run_tool_loop(llm, messages, toolkit, policies=policies):
        if kind == "content":
            final = str(payload)
    return final
