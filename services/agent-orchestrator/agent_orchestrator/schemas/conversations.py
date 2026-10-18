"""对话域 DTO(pydantic):与 ORM 模型分离,传输契约不泄漏存储细节。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_orchestrator.schemas.runs import RunRead


class ConversationCreate(BaseModel):
    """建对话:标题可空(首条消息会自动定题)。"""

    title: str | None = Field(default=None, max_length=200)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None


class ConversationPage(BaseModel):
    """会话列表分页:keyset 游标,避免 offset 漂移。"""

    items: list[ConversationRead]
    next_cursor: str | None = None


class ConversationUpdate(BaseModel):
    """会话局部更新:重命名 / 归档 / 取消归档(至少一项)。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None


class ConversationDetail(ConversationRead):
    """详情:附当前进行中的 run(前端据此接 SSE 事件流)。

    active_run_id 的口径与对话页的"进行中"一致:
    completed/failed/blocked 之外的状态都算(blocked 需人工 resume,
    新消息会另开 run;且 blocked 的 SSE 不会自动收尾,不该挂流)。
    """

    active_run_id: str | None


class AttachmentRead(BaseModel):
    """附件元数据(正文走 /attachments/{id} 下载,不塞进消息 JSON)。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    message_id: str | None
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class MessageCreate(BaseModel):
    """发消息:content 即对话内容/任务目标;mode 是"发送方式"。

    mode 只表达用户希望怎么发,实际怎么处理由分流决定、见
    MessagePostResult.mode:auto = 自动分流(默认)/ chat = 直接对话 /
    task = 作为任务执行(建 run 或并入当前 run 的追加指令)。
    """

    content: str = Field(min_length=1, max_length=2000)
    mode: Literal["auto", "chat", "task"] = "auto"
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    skill_id: str | None = Field(default=None, max_length=80)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    run_id: str | None
    role: str
    content: str
    created_at: datetime


class MessagePostResult(BaseModel):
    """发消息的结果:前端据此决定接哪个 run 的 SSE、是否刷新对话列表。

    - mode=chat:简单对话——reply 是 assistant 的直接回复,run 为空(没建任务);
    - mode=run_created:新开了任务——run 是新 run,reply 为空;
    - mode=instruction_queued:并入进行中的任务——run 是该 run,reply 为空。
    """

    message: MessageRead
    run: RunRead | None = None
    reply: MessageRead | None = None
    mode: Literal["chat", "run_created", "instruction_queued"]
