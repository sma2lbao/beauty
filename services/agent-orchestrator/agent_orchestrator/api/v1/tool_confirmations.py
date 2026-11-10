"""对话内写操作确认端点:确认执行 / 取消待确认的写工具调用。

确认链路主体在 services/tool_confirmations:chat 里 risk=write 的调用只落 pending
(不执行),这里以 approved=True 重放同一个 tool_call(arguments 原样),
把结果落成 assistant 消息并回填确认行。

口径:
- 不存在/跨账号一律 404 tool_confirmation.not_found(不泄漏存在性);
- 过期 409 tool_confirmation.expired、已确认/已取消 409 tool_confirmation.not_pending;
- 确认时用与 chat 同一套工具箱构建路径(允许 write、排除 danger),
  这样"用户看到的工具"与"确认时执行的工具"不会漂移。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_account_id,
    get_session,
    get_tool_extras_factory,
    get_toolkit_factory,
)
from agent_orchestrator.schemas.conversations import (
    ToolConfirmationCancelResult,
    ToolConfirmationConfirmRequest,
    ToolConfirmationConfirmResult,
)
from agent_orchestrator.services import tool_confirmations as confirmations_service
from agent_orchestrator.services.conversation_targets import require_conversation
from agent_orchestrator.services.conversations_persist import _toolkit_for
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context

router = APIRouter(prefix="/conversations", tags=["conversations"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
AccountId = Annotated[str, Depends(get_account_id)]
ToolkitFactory = Annotated[Any, Depends(get_toolkit_factory)]
ToolExtrasFactory = Annotated[Any, Depends(get_tool_extras_factory)]


@router.post(
    "/{conversation_id}/tool-confirmations/{confirmation_id}/confirm",
    response_model=ToolConfirmationConfirmResult,
)
async def confirm_tool_call(
    conversation_id: str,
    confirmation_id: str,
    session: DBSession,
    account_id: AccountId,
    toolkit_factory: ToolkitFactory,
    tool_extras_factory: ToolExtrasFactory,
    body: ToolConfirmationConfirmRequest | None = None,
) -> ToolConfirmationConfirmResult:
    """确认并执行待确认的写调用(重复确认 409,不重复执行)。"""
    conversation = await require_conversation(session, account_id, conversation_id)
    with bind_tool_context(
        ToolContext(
            account_id=account_id,
            conversation_id=conversation.id,
            workspace_id=conversation.workspace_id,
        )
    ):
        # 与发消息路径同一套装配(允许 write、排除 danger):用户看到的工具
        # 与确认时执行的工具不会漂移;装配期会读连接器目录与 MCP extras。
        toolkit = await _toolkit_for(
            toolkit_factory, tool_extras_factory, account_id, chat=True
        )
        outcome = await confirmations_service.confirm(
            session,
            account_id=account_id,
            conversation_id=conversation.id,
            confirmation_id=confirmation_id,
            toolkit=toolkit,
            grant=body.grant if body is not None else "once",
        )
    return ToolConfirmationConfirmResult(
        confirmation_id=outcome.confirmation.id,
        status="confirmed",
        ok=outcome.ok,
        error_code=outcome.error_code,
        result_text=outcome.confirmation.result_text,
        message_id=outcome.message.id,
    )


@router.post(
    "/{conversation_id}/tool-confirmations/{confirmation_id}/cancel",
    response_model=ToolConfirmationCancelResult,
)
async def cancel_tool_call(
    conversation_id: str,
    confirmation_id: str,
    session: DBSession,
    account_id: AccountId,
) -> ToolConfirmationCancelResult:
    """取消待确认的写调用(不执行工具、不落 assistant 消息)。"""
    conversation = await require_conversation(session, account_id, conversation_id)
    confirmation = await confirmations_service.cancel(
        session,
        account_id=account_id,
        conversation_id=conversation.id,
        confirmation_id=confirmation_id,
    )
    return ToolConfirmationCancelResult(
        confirmation_id=confirmation.id, status="cancelled"
    )
