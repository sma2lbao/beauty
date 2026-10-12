"""对外 DTO(pydantic)。与 ORM 模型分离:传输契约不泄漏存储细节。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

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


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    goal: str
    status: RunStatus
    rounds: int
    acceptance_criteria: list[str]
    blocked_reason: str | None
    created_at: datetime


class RunEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    seq: int
    type: str
    payload: dict[str, Any]
    occurred_at: str
