"""conversation 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import get_run_graph, get_session, get_tenant_id
from agent_orchestrator.schemas.conversations import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageCreate,
    MessagePostResult,
    MessageRead,
)
from agent_orchestrator.schemas.runs import RunRead
from agent_orchestrator.services import conversations as conversations_service

router = APIRouter(prefix="/conversations", tags=["conversations"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate | None, session: DBSession, tenant_id: TenantId
) -> ConversationRead:
    """建对话:标题可空(首条消息自动定题)。"""
    conversation = await conversations_service.create_conversation(
        session, tenant_id, (body or ConversationCreate()).title
    )
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    session: DBSession,
    tenant_id: TenantId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ConversationRead]:
    """租户内对话列表,最近活跃在前(对话页侧栏)。"""
    conversations = await conversations_service.list_conversations(
        session, tenant_id, limit
    )
    return [ConversationRead.model_validate(item) for item in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, session: DBSession, tenant_id: TenantId
) -> ConversationDetail:
    """对话详情:附当前进行中的 run(前端据此接 SSE 事件流)。"""
    conversation = await conversations_service.require_conversation(
        session, tenant_id, conversation_id
    )
    active = await conversations_service.active_run(session, tenant_id, conversation.id)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        active_run_id=active.id if active else None,
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: str, session: DBSession, tenant_id: TenantId
) -> list[MessageRead]:
    """对话消息回放:按时间升序(user/assistant 混排)。"""
    messages = await conversations_service.list_messages(
        session, tenant_id, conversation_id
    )
    return [MessageRead.model_validate(item) for item in messages]


@router.post(
    "/{conversation_id}/messages",
    response_model=MessagePostResult,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    body: MessageCreate,
    conversation_id: str,
    session: DBSession,
    tenant_id: TenantId,
    graph: RunGraph,
) -> MessagePostResult:
    """发消息:进行中的 run → 追加指令(§6.5);否则新开一跑并立即规划。"""
    message, run, mode = await conversations_service.post_message(
        session, graph, tenant_id, conversation_id, body.content
    )
    return MessagePostResult(
        message=MessageRead.model_validate(message),
        run=RunRead.model_validate(run),
        mode=mode,
    )
