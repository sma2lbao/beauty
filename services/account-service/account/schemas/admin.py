"""管理面契约(admin-plane §3.2/§3.3):账号、配额/角色/状态与审计日志。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from account.schemas.insights import UsageSummary


class AdminUsage(BaseModel):
    """账号列表里展示的本月用量(与 /usage/summary 同口径)。"""

    month_tokens: int = 0
    month_events: int = 0
    month_cost_usd: float = 0.0
    unpriced_events: int = 0


class AdminAccount(BaseModel):
    """管理面账号视图:档案 + 角色 + 本月用量。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    display_name: str | None
    status: str
    role: str
    quota: dict[str, Any]
    created_at: datetime
    usage: AdminUsage


class AdminAccountList(BaseModel):
    items: list[AdminAccount]
    total: int


class AdminAccountDetail(BaseModel):
    """详情页:账号视图 + 完整用量汇总(含 by_model)。"""

    account: AdminAccount
    usage: UsageSummary


class QuotaUpdate(BaseModel):
    """PATCH quota:省略 = 保持,显式 null = 清除;整数须 >= 1。"""

    model_config = ConfigDict(extra="forbid")

    monthly_tokens: int | None = Field(default=None, ge=1)
    max_concurrent_runs: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _require_any_field(self) -> QuotaUpdate:
        if not self.model_fields_set:
            raise ValueError("至少提供一个配额字段")
        return self


class RoleUpdate(BaseModel):
    """PATCH role:只允许 user/admin 两个取值。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "admin"]


class StatusUpdate(BaseModel):
    """PATCH status:只允许 active/disabled。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["active", "disabled"]


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_account_id: str
    actor_email: str
    action: str
    target_type: str
    target_id: str
    status: str
    detail: dict[str, Any]
    request_id: str | None
    ip: str | None
    user_agent: str | None
    created_at: datetime


class AuditLogList(BaseModel):
    items: list[AuditLogRead]
    total: int
