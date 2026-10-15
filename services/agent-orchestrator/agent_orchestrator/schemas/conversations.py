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


class ConversationDetail(ConversationRead):
    """详情:附当前进行中的 run(前端据此接 SSE 事件流)。

    active_run_id 的口径与对话页的"进行中"一致:
    completed/failed/blocked 之外的状态都算(blocked 需人工 resume,
    新消息会另开 run;且 blocked 的 SSE 不会自动收尾,不该挂流)。
    """

    active_run_id: str | None


class MessageCreate(BaseModel):
    """发消息:content 即任务目标或追加指令(§6.5)。"""

    content: str = Field(min_length=1, max_length=2000)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    run_id: str | None
    role: str
    content: str
    created_at: datetime


class MessagePostResult(BaseModel):
    """发消息的结果:前端据此决定接哪个 run 的 SSE、是否刷新对话列表。"""

    message: MessageRead
    run: RunRead
    mode: Literal["run_created", "instruction_queued"]
