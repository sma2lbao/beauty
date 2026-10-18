"""conversation 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。

发消息有两个入口,共用同一套分流与上下文口径:

- POST /messages:一次拿全文(脚本/内部调用省事);
- POST /messages/stream:SSE 逐段增量(对话页"边输出边显示"用)。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote

from common.errors import AppError, NotFoundError
from database.session import session_scope
from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.api.deps import (
    get_llm_client,
    get_run_graph,
    get_session,
    get_settings,
    get_skills,
    get_tenant_id,
)
from agent_orchestrator.schemas.conversations import (
    AttachmentRead,
    ConversationCreate,
    ConversationDetail,
    ConversationPage,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessagePostResult,
    MessageRead,
)
from agent_orchestrator.schemas.runs import RunRead
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import conversations as conversations_service
from agent_orchestrator.services import triage
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.skills import SkillRegistry

router = APIRouter(prefix="/conversations", tags=["conversations"])
attachments_router = APIRouter(prefix="/attachments", tags=["attachments"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]
LLM = Annotated[LLMClient | None, Depends(get_llm_client)]
SkillDep = Annotated[SkillRegistry, Depends(get_skills)]


def _resolve_skill(skills: SkillRegistry, skill_id: str | None):
    """消息可选绑定 Skill:未知 id 在落库前 404,避免建出半残 run。"""
    if not skill_id:
        return None
    skill = skills.get(skill_id)
    if skill is None:
        raise NotFoundError("skill.not_found", "Skill 不存在")
    return skill

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


@router.get("", response_model=ConversationPage)
async def list_conversations(
    session: DBSession,
    tenant_id: TenantId,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=100)] = None,
    archived: bool = False,
    cursor: str | None = None,
) -> ConversationPage:
    """租户内对话列表,最近活跃在前(对话页侧栏)。

    q 按标题搜索;archived=true 只看归档;cursor 为上一页返回的 next_cursor。
    """
    conversations, next_cursor = await conversations_service.list_conversations(
        session,
        tenant_id,
        limit,
        q=q,
        archived=archived,
        cursor=cursor,
    )
    return ConversationPage(
        items=[ConversationRead.model_validate(item) for item in conversations],
        next_cursor=next_cursor,
    )


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: str,
    body: ConversationUpdate,
    session: DBSession,
    tenant_id: TenantId,
) -> ConversationRead:
    """局部更新会话:重命名 / 归档 / 取消归档(至少给一项)。"""
    if body.title is None and body.archived is None:
        raise AppError("conversation.empty_update", "没有可更新的字段")
    conversation = None
    if body.title is not None:
        conversation = await conversations_service.rename_conversation(
            session, tenant_id, conversation_id, body.title
        )
    if body.archived is not None:
        conversation = await conversations_service.set_archived(
            session, tenant_id, conversation_id, body.archived
        )
    if conversation is None:  # pragma: no cover - 上面的空更新已拦截
        raise AppError("conversation.empty_update", "没有可更新的字段")
    return ConversationRead.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str, session: DBSession, tenant_id: TenantId
) -> None:
    """软删除会话(保留审计数据;列表/详情/消息随即 404)。"""
    await conversations_service.delete_conversation(
        session, tenant_id, conversation_id
    )


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
    "/{conversation_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    conversation_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: Annotated[Any, Depends(get_settings)],
    file: Annotated[UploadFile, File()],
) -> AttachmentRead:
    """上传附件:本地最小版,内容存 DB;类型/大小在 service 校验。"""
    data = await file.read()
    attachment = await attachments_service.create_attachment(
        session,
        tenant_id,
        conversation_id,
        filename=file.filename,
        content_type=file.content_type,
        data=data,
        max_bytes=settings.max_attachment_bytes,
    )
    return AttachmentRead.model_validate(attachment)


@router.get(
    "/{conversation_id}/attachments", response_model=list[AttachmentRead]
)
async def list_attachments(
    conversation_id: str, session: DBSession, tenant_id: TenantId
) -> list[AttachmentRead]:
    attachments = await attachments_service.list_attachments(
        session, tenant_id, conversation_id
    )
    return [AttachmentRead.model_validate(item) for item in attachments]


@attachments_router.get("/{attachment_id}")
async def download_attachment(
    attachment_id: str, session: DBSession, tenant_id: TenantId
) -> Response:
    """下载附件:附件接口不内联到消息 JSON,下载单独走这里。"""
    attachment = await attachments_service.require_attachment(
        session, tenant_id, attachment_id
    )
    filename = quote(attachment.filename)
    return Response(
        content=attachment.content,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
        },
    )


@attachments_router.delete(
    "/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_attachment(
    attachment_id: str, session: DBSession, tenant_id: TenantId
) -> None:
    await attachments_service.delete_attachment(session, tenant_id, attachment_id)


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
    settings: Annotated[Any, Depends(get_settings)],
    skills: SkillDep,
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
        attachment_ids=body.attachment_ids,
        jobs_enabled=settings.jobs_enabled,
        skill=_resolve_skill(skills, body.skill_id),
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
    settings: Annotated[Any, Depends(get_settings)],
    skills: SkillDep,
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
                attachment_ids=body.attachment_ids,
                jobs_enabled=settings.jobs_enabled,
                skill=_resolve_skill(skills, body.skill_id),
            )
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
