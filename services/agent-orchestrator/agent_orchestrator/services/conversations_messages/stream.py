"""流式单轮:把一次模型对话跑成事件流(_stream_turn)。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.conversations import ChatMessage, Conversation
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import approval as approval_service
from agent_orchestrator.services import chat, tool_confirmations
from agent_orchestrator.services import hooks as hooks_service
from agent_orchestrator.services import usage as usage_service
from agent_orchestrator.services.conversations_context import _maybe_extract_memories
from agent_orchestrator.services.conversations_persist import (
    _persist_reply,
    _toolkit_for,
)
from agent_orchestrator.services.llm import LLMClient, UpstreamError
from agent_orchestrator.services.llm_binding import bind_dialogue_llm
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context

if TYPE_CHECKING:  # 仅用于类型标注,避免 services 层与工具层的导入环。
    from collections.abc import Awaitable, Callable

    from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

    ToolkitFactory = Callable[[str], ToolRegistry]
    #: 外部工具(MCP/skill_run)工厂:按账号现取,与 run 执行图共用
    ExtrasFactory = Callable[[str], Awaitable[list[ToolSpec]]]
async def _stream_turn(
    factory: async_sessionmaker[AsyncSession],
    llm: LLMClient | None,
    *,
    account_id: str,
    conversation: Conversation,
    message: ChatMessage,
    resolved_model: str | None,
    outcome: str,
    run: Run | None,
    prompt: list[dict[str, Any]],
    mcp_server_ids: list[str] | None,
    toolkit_factory: ToolkitFactory | None,
    extras_factory: ExtrasFactory | None,
    settings: Settings | None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """流式产出本轮增量(不占事务);发消息与重写共用第二段。

    - delta:正文增量(仅 chat;工具轮也是真流式,逐段原样转发);
    - tool:chat 内只读工具调用(对话内 function-calling,§6.8);
    - done:收尾(chat 带落库后的 assistant 回复);
    - error:失败(流已按 200 开头,只能用帧报错)。

    工具轮的正文不再"跑完整个工具循环再按字补发":每轮模型输出边产出边进
    delta 帧,工具事件夹在轮次之间;中间轮次的过渡说明同样实时下发,并随
    parts 一起落库,最终 assistant 回复 = 各轮正文拼接(与客户端看到的逐帧
    内容完全一致)。

    客户端中途断开时,已产出的增量仍会落库(不丢半截回复);全部失败则不留
    assistant 消息,由对话页展示错误。
    """
    if run is not None or llm is None:
        # 任务路径的进展由 run 自己的事件流呈现,这里不重复
        yield "done", {"mode": outcome, "reply": None}
        return
    conversation_id = conversation.id
    # 第二段:流式产出增量(不占事务);顺带采集本轮用量(P0-1,run_id 为空)
    collector = usage_service.UsageCollector()
    usage_token = usage_service.bind(collector)
    parts: list[str] = []
    failure: UpstreamError | None = None
    reply: ChatMessage | None = None
    try:
        # 对话内工具执行的来源上下文(stdio MCP 沙箱按对话复用);
        # 生成器被 aclose 时 with 会退出,reset contextvar,不跨请求泄漏
        with (
            bind_dialogue_llm(
                account_id=account_id,
                conversation_id=conversation_id,
                model=resolved_model,
            ),
            bind_tool_context(
                ToolContext(
                    account_id=account_id,
                    conversation_id=conversation_id,
                    workspace_id=conversation.workspace_id,
                    mcp_server_ids=tuple(mcp_server_ids or ()),
                )
            ),
        ):
            toolkit = await _toolkit_for(
                toolkit_factory, extras_factory, account_id, chat=True
            )
            if toolkit is None or len(toolkit) == 0:
                # 无工具:保持原路径——真正的流式增量(边产出边显示)
                async for delta in llm.stream(prompt):
                    parts.append(delta)
                    yield "delta", {"text": delta}
            else:
                # 对话内 function-calling:每轮正文真流式下发、工具事件实时转发;
                # 中间轮(模型过渡说明)的正文也下发并落库,不再只保留最后一轮。
                # 第一段事务已在 yield meta 前结束,此处必须开新事务:
                # 复用已 close 的 session 会隐式 autobegin 再签出连接,无人归还
                async with session_scope(factory) as policy_session:
                    policies = await approval_service.load_policies(
                        policy_session, account_id, conversation_id
                    )
                    hooks = await hooks_service.load_hooks(policy_session, account_id)
                # 写操作就地确认:流式路径不在长事务里,每条 pending 独立短事务落库
                confirmations = tool_confirmations.StreamConfirmationRecorder(
                    factory, account_id=account_id, conversation_id=conversation_id
                )
                async for kind, payload in chat.run_tool_loop(
                    llm,
                    prompt,
                    toolkit,
                    policies=policies,
                    stream=True,
                    confirmations=confirmations,
                    hooks=hooks,
                    hook_base={
                        "account_id": account_id,
                        "conversation_id": conversation_id,
                    },
                ):
                    if kind == "delta":
                        delta = str(payload["text"])
                        parts.append(delta)
                        yield "delta", {"text": delta}
                    elif kind == "tool":
                        yield "tool", payload
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
            account_id=account_id, run_id=None, samples=collector.samples
        )
    if failure is not None:
        yield "error", {"code": failure.code, "message": failure.message}
        return
    yield "done", {"mode": outcome, "reply": reply}
    # 记忆抽取放到 done 之后:客户端先拿到完成帧,抽取慢不会拖住收尾;独立短事务,
    # 失败只 warning(客户端断开导致生成器关闭时跳过本轮抽取,不影响已落库回复)。
    if reply is not None:
        async with session_scope(factory) as extract_session:
            await _maybe_extract_memories(
                extract_session,
                llm,
                account_id=account_id,
                conversation_id=conversation_id,
                workspace_id=conversation.workspace_id,
                settings=settings,
            )
