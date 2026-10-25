"""conversation 发消息端点:一次全文 + SSE 流式(共用同一套分流与上下文口径)。

从 conversations.py 拆出(R-29):会话 CRUD/附件留在原模块,消息发送单独成组,
两个入口的差异只在"怎么把回复交给调用方",分流、技能/MCP 解析与租户校验只有一份。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from common.artifacts import ArtifactStore
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.api.deps import (
    get_artifact_store,
    get_container,
    get_file_downloader,
    get_llm_client,
    get_run_graph,
    get_session,
    get_settings,
    get_skills,
    get_tenant_id,
    get_tool_extras_factory,
    get_toolkit_factory,
    get_workspace_client,
)
from agent_orchestrator.core.sse import named_event_frame
from agent_orchestrator.schemas.conversations import (
    MessageCreate,
    MessagePostResult,
    MessageRead,
)
from agent_orchestrator.schemas.runs import RunRead
from agent_orchestrator.services import conversations as conversations_service
from agent_orchestrator.services import triage
from agent_orchestrator.services.file_capture import FileDownloader
from agent_orchestrator.services.llm import LLMClient
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace

router = APIRouter(prefix="/conversations", tags=["conversations"])

DBSession = Annotated[AsyncSession, Depends(get_session)]
TenantId = Annotated[str, Depends(get_tenant_id)]
RunGraph = Annotated[CompiledStateGraph, Depends(get_run_graph)]
LLM = Annotated[LLMClient | None, Depends(get_llm_client)]
SettingsDep = Annotated[Any, Depends(get_settings)]
SkillsDep = Annotated[SkillRegistry, Depends(get_skills)]
ToolkitFactory = Annotated[Any, Depends(get_toolkit_factory)]
ToolExtrasFactory = Annotated[Any, Depends(get_tool_extras_factory)]
ArtifactStoreDep = Annotated[ArtifactStore | None, Depends(get_artifact_store)]
WorkspaceDep = Annotated[Workspace | None, Depends(get_workspace_client)]
FileDownloaderDep = Annotated[FileDownloader | None, Depends(get_file_downloader)]

#: SSE 标准头:不缓存、禁代理缓冲(否则增量会被攒成一坨)
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


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
    settings: SettingsDep,
    skills: SkillsDep,
    toolkit_factory: ToolkitFactory,
    tool_extras_factory: ToolExtrasFactory,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
) -> MessagePostResult:
    """发消息:先分流(§6.7)——简单对话直接答;复杂任务建 run 或并入当前 run。"""
    target = await conversations_service.resolve_message_target(
        session,
        tenant_id,
        skills=skills,
        settings=settings,
        skill_id=body.skill_id,
        mcp_server_ids=body.mcp_server_ids,
    )
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
        skill=target.skill,
        mcp_server_ids=target.mcp_server_ids,
        mcp_reference_note=target.mcp_reference_note,
        toolkit_factory=toolkit_factory,
        extras_factory=tool_extras_factory,
        store=store,
        workspace=workspace,
        downloader=downloader,
        max_download_files=settings.artifact_download_max_files,
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
            elif name == "tool":
                data = {
                    "name": payload["name"],
                    "arguments": payload["arguments"],
                    "ok": payload["ok"],
                }
                # P1 审计字段(可选):有则带,没有则省略键,旧客户端不受影响。
                for key in (
                    "risk",
                    "decision",
                    "error_code",
                    "duration_ms",
                    "result_chars",
                    "truncated",
                ):
                    value = payload.get(key)
                    if value is not None:
                        data[key] = value
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
            yield named_event_frame(name, data)

    return _gen()


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    body: MessageCreate,
    conversation_id: str,
    request: Request,
    tenant_id: TenantId,
    graph: RunGraph,
    llm: LLM,
    settings: SettingsDep,
    skills: SkillsDep,
    toolkit_factory: ToolkitFactory,
    tool_extras_factory: ToolExtrasFactory,
    store: ArtifactStoreDep,
    workspace: WorkspaceDep,
    downloader: FileDownloaderDep,
) -> StreamingResponse:
    """SSE 流式发消息:meta(分流结果)→ delta/tool(增量/工具调用)→ done;
    失败以 error 帧收尾。

    模型没开且显式要 chat 时在开流前就 409(否则只能发一帧错误,状态码已是 200)。
    """
    triage.require_available(body.mode, llm)
    container = get_container(request)
    assert container.session_factory is not None
    factory = container.session_factory
    # 会话存在性/技能/MCP 解析放在开流前:未知 id 直接 404,而不是开流后只能发错误帧
    target = await conversations_service.resolve_stream_target(
        factory,
        tenant_id,
        conversation_id,
        skills=skills,
        settings=settings,
        skill_id=body.skill_id,
        mcp_server_ids=body.mcp_server_ids,
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
                skill=target.skill,
                mcp_server_ids=target.mcp_server_ids,
                mcp_reference_note=target.mcp_reference_note,
                toolkit_factory=toolkit_factory,
                extras_factory=tool_extras_factory,
                store=store,
                workspace=workspace,
                downloader=downloader,
                max_download_files=settings.artifact_download_max_files,
            )
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
