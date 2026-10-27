"""conversation 端点:参数校验 → 调 service → 返回 schema,不写业务逻辑。

会话 CRUD、消息回放与附件收发在这里;发消息(一次全文 / SSE 流式)拆到
conversation_messages.py(R-29),两者共用同一套分流与上下文口径。
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from common.artifacts import ArtifactStore
from common.errors import AppError, UpstreamError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_artifact_store,
    get_session,
    get_settings,
    get_tenant_id,
    get_tool_client,
    get_workspace_client,
)
from agent_orchestrator.schemas.conversations import (
    AttachmentRead,
    ConversationCreate,
    ConversationDetail,
    ConversationPage,
    ConversationRead,
    ConversationUpdate,
    MessageRead,
)
from agent_orchestrator.schemas.tool_calls import (
    ConversationToolCallPage,
    ConversationToolCallRead,
)
from agent_orchestrator.services import attachments as attachments_service
from agent_orchestrator.services import conversations as conversations_service
from agent_orchestrator.services.tool_client import ToolClient
from agent_orchestrator.services.workspace import Workspace

router = APIRouter(prefix="/conversations", tags=["conversations"])
attachments_router = APIRouter(prefix="/attachments", tags=["attachments"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
ArtifactStoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]
WorkspaceDep = Annotated[Workspace | None, Depends(get_workspace_client)]
SettingsDep = Annotated[Any, Depends(get_settings)]
ToolClientDep = Annotated[ToolClient | None, Depends(get_tool_client)]


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate | None,
    session: DBSession,
    tenant_id: TenantId,
    workspace: WorkspaceDep,
) -> ConversationRead:
    """建对话:标题可空(首条消息自动定题)。

    工作区归属:body.workspace_id 显式给就校验(不存在 404 workspace.not_found),
    不给则落租户默认工作区——"对话必须在工作区中"由服务层兜底。
    """
    data = body or ConversationCreate()
    conversation = await conversations_service.create_conversation(
        session,
        tenant_id,
        data.title,
        workspace_id=data.workspace_id,
        workspace=workspace,
    )
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=ConversationPage)
async def list_conversations(
    session: DBSession,
    tenant_id: TenantId,
    workspace: WorkspaceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=100)] = None,
    archived: bool = False,
    cursor: str | None = None,
    workspace_id: Annotated[str | None, Query(max_length=32)] = None,
) -> ConversationPage:
    """租户内对话列表,最近活跃在前(对话页侧栏)。

    q 按标题搜索;archived=true 只看归档;cursor 为上一页返回的 next_cursor;
    workspace_id 给出时只看该工作区的会话(不给则全租户,并顺手把历史空归属
    会话回填到默认工作区)。
    """
    conversations, next_cursor = await conversations_service.list_conversations(
        session,
        tenant_id,
        limit,
        q=q,
        archived=archived,
        cursor=cursor,
        workspace_id=workspace_id,
        workspace=workspace,
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
    await conversations_service.delete_conversation(session, tenant_id, conversation_id)


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
        workspace_id=conversation.workspace_id,
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


@router.get("/{conversation_id}/tool-calls", response_model=ConversationToolCallPage)
async def list_conversation_tool_calls(
    conversation_id: str,
    session: DBSession,
    tenant_id: TenantId,
    tool_client: ToolClientDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationToolCallPage:
    """会话级工具调用审计:跨 run 聚合,附服务端生成的 unified diff。

    先校验会话归属(跨租户一律 404,不泄漏存在性)。tool-service 未启用或
    不可达时**不炸请求**:返回 200 + degraded=true 的空页,前端据此回退到
    从事件参数本地重建补丁。
    """
    await conversations_service.require_conversation(
        session, tenant_id, conversation_id
    )
    if tool_client is None:
        return ConversationToolCallPage(limit=limit, offset=offset, degraded=True)
    try:
        payload = await tool_client.list_tool_calls(
            tenant_id, conversation_id=conversation_id, limit=limit, offset=offset
        )
        items = [
            ConversationToolCallRead.model_validate(item)
            for item in payload.get("items", [])
        ]
    except (UpstreamError, ValueError):
        return ConversationToolCallPage(limit=limit, offset=offset, degraded=True)
    total = payload.get("total")
    return ConversationToolCallPage(
        items=items,
        total=total if isinstance(total, int) else len(items),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{conversation_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    conversation_id: str,
    session: DBSession,
    tenant_id: TenantId,
    settings: SettingsDep,
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


@router.get("/{conversation_id}/attachments", response_model=list[AttachmentRead])
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


@attachments_router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: str, session: DBSession, tenant_id: TenantId
) -> None:
    await attachments_service.delete_attachment(session, tenant_id, attachment_id)
