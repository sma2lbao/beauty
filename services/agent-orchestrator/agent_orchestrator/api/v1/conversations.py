"""conversation 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。

发消息有两个入口,共用同一套分流与上下文口径:

- POST /messages:一次拿全文(脚本/内部调用省事);
- POST /messages/stream:SSE 逐段增量(对话页"边输出边显示"用)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from database.session import session_scope
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.api.deps import (
    get_llm_client,
    get_run_graph,
    get_session,
    get_tenant_id,
)
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
from agent_orchestrator.services import triage
from agent_orchestrator.services.llm import LLMClient

router = APIRouter(prefix="/conversations", tags=["conversations"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]
LLM = Annotated[LLMClient | None, Depends(get_llm_client)]

#: SSE 标准头:不缓存、禁代理缓冲(否则增量会被攒成一坨)
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse(name: str, data: dict[str, Any]) -> str:
    """一个具名 SSE 帧(前端按 event 名分发:meta / delta / done / error)。"""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    llm: LLM,
) -> MessagePostResult:
    """发消息:先分流(§6.7)——简单对话直接答;复杂任务建 run 或并入当前 run。"""
    message, run, reply, mode = await conversations_service.post_message(
        session,
        graph,
        llm,
        tenant_id,
        conversation_id,
        body.content,
        mode=body.mode,
    )
    return MessagePostResult(
        message=MessageRead.model_validate(message),
        run=RunRead.model_validate(run) if run is not None else None,
        reply=MessageRead.model_validate(reply) if reply is not None else None,
        mode=mode,
    )


def _message_frames(
    events: AsyncIterator[tuple[str, dict[str, Any]]],
) -> AsyncIterator[str]:
    """service 事件 → SSE 帧(ORM/schema 的转换只在这一层做)。"""

    async def _gen() -> AsyncIterator[str]:
        async for name, payload in events:
            if name == "meta":
                run = payload.get("run")
                data: dict[str, Any] = {
                    "mode": payload["mode"],
                    "message": MessageRead.model_validate(
                        payload["message"]
                    ).model_dump(mode="json"),
                    "run": (
                        RunRead.model_validate(run).model_dump(mode="json")
                        if run is not None
                        else None
                    ),
                }
            elif name == "delta":
                data = {"text": payload["text"]}
            elif name == "done":
                reply = payload.get("reply")
                data = {
                    "mode": payload["mode"],
                    "reply": (
                        MessageRead.model_validate(reply).model_dump(mode="json")
                        if reply is not None
                        else None
                    ),
                }
            else:  # error
                data = {"code": payload["code"], "message": payload["message"]}
            yield _sse(name, data)

    return _gen()


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    body: MessageCreate,
    conversation_id: str,
    request: Request,
    tenant_id: TenantId,
    graph: RunGraph,
    llm: LLM,
) -> StreamingResponse:
    """SSE 流式发消息:meta(分流结果)→ delta(增量)→ done;失败以 error 帧收尾。

    模型没开且显式要 chat 时在开流前就 409(否则只能发一帧错误,状态码已是 200)。
    """
    triage.require_available(body.mode, llm)
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_scope(factory) as session:
        await conversations_service.require_conversation(  # 404 校验
            session, tenant_id, conversation_id
        )
    return StreamingResponse(
        _message_frames(
            conversations_service.stream_message(
                factory,
                graph,
                llm,
                tenant_id,
                conversation_id,
                body.content,
                mode=body.mode,
            )
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
