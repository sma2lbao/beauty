"""对外 DTO(pydantic)。与 ORM 模型分离:传输契约不泄漏存储细节。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    """run 状态机(架构 §6.2):goal 语义,blocked 可 resume。"""

    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class RunCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    # 对话归属:由对话消息触发的 run 带;控制台直建为空
    conversation_id: str | None = Field(default=None, min_length=1, max_length=32)
    # 可选 Skill(M1):创建时快照 manifest,plan/execute 注入约束与工具白名单
    skill_id: str | None = Field(default=None, max_length=80)


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    goal: str
    status: RunStatus
    rounds: int
    attempts: int
    acceptance_criteria: list[str]
    status_reason: str | None
    parent_run_id: str | None = None
    conversation_id: str | None = None
    skill_id: str | None = None
    created_at: datetime


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: str


class PlanUpdate(BaseModel):
    """审批前编辑计划(§6.1:plan 是可编辑工件)。"""

    plan_markdown: str = Field(min_length=1, max_length=20000)


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ConfirmRequest(BaseModel):
    """写操作确认(§6.2):放行则执行,拒绝则跳过并继续验收。"""

    action: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class InstructionCreate(BaseModel):
    """运行中追加指令(§6.5):在下一个图边界(规划/放行/确认)被消费。"""

    content: str = Field(min_length=1, max_length=2000)


class InstructionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    content: str
    status: str
    created_at: datetime


class PlanRead(BaseModel):
    run_id: str
    status: RunStatus
    plan_markdown: str


class ArtifactRead(BaseModel):
    """交付物读取契约(§7):内容已装载,附存储后端与体量。"""

    id: str
    run_id: str
    kind: str
    title: str
    content: str
    storage: str
    size_bytes: int
    created_at: datetime
