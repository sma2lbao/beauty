"""用量与 BYOK 契约(M0-11 本地版)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UsageByModel(BaseModel):
    model: str
    tokens: int
    events: int


class UsageSummary(BaseModel):
    today_tokens: int
    month_tokens: int
    today_events: int
    month_events: int
    by_model: list[UsageByModel]


class UsageEventCreate(BaseModel):
    """内部上报事件(orchestrator/seed 用;X-Internal-Secret 保护)。"""

    run_id: str | None = Field(default=None, max_length=32)
    model: str = Field(default="unknown", max_length=128)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class UsageQuota(BaseModel):
    """租户配额快照(内部读取用):未配置的维度为 None,表示不限制。"""

    monthly_tokens: int | None = None
    max_concurrent_runs: int | None = None
    used_tokens: int = 0


class BYOKUpsert(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)


class BYOKRead(BaseModel):
    model_config = {"from_attributes": True}

    provider: str
    masked_key: str
    key_hint: str
    updated_at: datetime
