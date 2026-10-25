"""直接对话回复(§6.7):不建 run、不进执行图,直接用模型答一条。

与任务路径的分工:chat 只回答——不产生副作用、不产出交付物、不需要审批;
模型若判断"这其实是任务",就在回复里引导用户改用任务发送。

对话内 function tools(只读):查资料类工具(文档/知识库/代码读取)
允许在 chat 里直接调用——问"知识库里怎么写的"不该被迫建 run;
写操作工具一律拒绝执行,回喂错误并引导改用「任务」(任务路径有确认闸 §6.2)。
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph.tool_results import (
    is_tool_error,
    parse_tool_error,
    tool_result_fields,
)
from agent_orchestrator.models.conversations import ChatMessage
from agent_orchestrator.repositories import MessageRepository
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import history
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.tools import ToolRisk

MAX_CHAT_TOOL_ROUNDS = 5  # 对话内 function-calling 轮次上限

#: tool 帧的 decision 文案(与执行图审计语义一致;前端归并 approved/confirmed)。
_DECISION_TEXT = {
    approval_service.ApprovalAction.AUTO: "auto",
    approval_service.ApprovalAction.CONFIRM: "confirm",
    approval_service.ApprovalAction.DENY: "denied",
}

CHAT_SYSTEM_PROMPT = (
    "你是对话助手。可以调用提供的只读工具查询信息(如文档、"
    "知识库),拿到结果后直接、简洁地回答(一般 2-5 句),不要输出执行计划、"
    "不要寒暄客套。写操作请提示用户改用「任务」发送——"
    "任务路径有人工确认。如果这个问题需要多步执行或产出交付物,"
    "也用一句话说明并提示用户改用「任务」发送。"
)


@dataclass(frozen=True)
class ChatPromptContext:
    """对话上下文(压缩摘要 + 长期记忆召回),由服务层提前算好。

    为什么由调用方算:压缩要调模型、记忆召回要查库,而 chat.prompt 是纯装配
    (非流式与流式共用同一口径)。context 为空时退回"只带近期原文"的老行为。
    """

    rows: list[ChatMessage] = field(default_factory=list)
    summary: str = ""
    memory_block: str = ""


async def prompt(
    session: AsyncSession,
    message: ChatMessage,
    *,
    reference_note: str = "",
    context: ChatPromptContext | None = None,
) -> list[dict[str, str]]:
    """直接对话的完整消息序列:系统提示 + 摘要/历史 + 本轮。

    非流式与流式共用——两条路径的上下文口径因此不会漂。
    reference_note 是本次消息显式引用 MCP server 的约束片段(空则不追加);
    context 带上压缩摘要与记忆召回(空则退化为只带近期原文,保持旧行为)。
    """
    if context is None:
        rows = await MessageRepository(session).list_before(
            message.tenant_id,
            message.conversation_id,
            before=message.created_at,
            exclude_id=message.id,
        )
        summary = ""
        memory_block = ""
    else:
        rows = context.rows
        summary = context.summary
        memory_block = context.memory_block
    system_prompt = CHAT_SYSTEM_PROMPT
    if memory_block:
        system_prompt = f"{system_prompt}\n\n{memory_block}"
    if reference_note:
        system_prompt = f"{system_prompt}\n\n{reference_note}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history.to_prompt(rows, summary=summary))
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

    产出 ("tool", {name, arguments, ok, risk, error_code, duration_ms}) 事件
    供流式层转发;最终一轮产出 ("content", 正文)。只读与受控写工具自动执行;
    **危险操作(DANGER)不执行**,
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
            # 远端工具箱回旧枚举字符串,本机注册表回 ToolRisk;统一成枚举再求值。
            risk = str(toolkit.risk(call.name))
            decision = approval_service.decide(policies, call.name, ToolRisk(risk))
            duration_ms = 0
            if decision is approval_service.ApprovalAction.DENY:
                output = _tool_error(f"工具 {call.name} 被租户审批策略禁止使用")
            elif decision is approval_service.ApprovalAction.CONFIRM:
                # 需要人工确认的动作在 chat 中不执行:回喂错误让模型引导走任务
                output = _tool_error(
                    "危险操作不允许在对话中执行,请提示用户改用「任务」发送"
                )
            else:
                started = time.perf_counter()
                # 对话没有审批流:approved 恒 False,写工具由服务端风险下限兜底。
                output = await toolkit.execute(
                    call.name, call.arguments, approved=False
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
            messages.append(_tool_message(call.id, output))
            result_fields = tool_result_fields(output)
            yield (
                "tool",
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "ok": not is_tool_error(output),
                    "risk": risk,
                    "decision": _DECISION_TEXT[decision],
                    "error_code": parse_tool_error(output),
                    "duration_ms": duration_ms,
                    "result_chars": len(output),
                    "truncated": result_fields.get("truncated"),
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
    context: ChatPromptContext | None = None,
) -> str:
    """给一条用户消息生成直接回复(一次拿全文,非流式路径)。

    toolkit 为空(未配置工具)时行为与原先完全一致:单轮直答。
    context 为空时只带近期原文(旧行为);服务层会传入压缩摘要与记忆召回。
    """
    messages = await prompt(
        session, message, reference_note=reference_note, context=context
    )
    if toolkit is None or len(toolkit) == 0:
        reply = await llm.complete(messages)
        return reply.content
    final = ""
    async for kind, payload in run_tool_loop(llm, messages, toolkit, policies=policies):
        if kind == "content":
            final = str(payload)
    return final
