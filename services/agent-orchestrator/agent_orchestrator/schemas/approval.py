"""审批策略的请求/响应模型(P0-4)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ApprovalPolicyIn(BaseModel):
    """一条策略:工具名(或 ``*``)+ 动作。"""

    tool: str = Field(min_length=1, max_length=120)
    action: Literal["auto", "confirm", "deny"]

    @field_validator("tool")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("tool 不能为空")
        return stripped


class ApprovalPolicyUpdate(BaseModel):
    """整表替换:提交的就是更新后的完整策略集。"""

    policies: list[ApprovalPolicyIn] = Field(default_factory=list)

    @field_validator("policies")
    @classmethod
    def _unique_tools(cls, value: list[ApprovalPolicyIn]) -> list[ApprovalPolicyIn]:
        tools = [item.tool for item in value]
        if len(tools) != len(set(tools)):
            raise ValueError("同一个工具名只能出现一次")
        return value


class ApprovalPolicyRead(BaseModel):
    tool: str
    action: str
