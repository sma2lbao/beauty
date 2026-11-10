"""审批策略的请求/响应模型(P0-4/P1-A)。"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

#: 参数匹配方式(与 services.approval.VALID_MATCHES 同值;schema 不反向 import 服务层)。
VALID_MATCHES = ("exact", "prefix", "glob")


class ApprovalPolicyIn(BaseModel):
    """一条策略:工具名(或 `*`)+ 动作 + 可选参数条件。

    `arg_name` 为空表示无条件规则(命中该工具的所有调用);非空时必须给出
    `arg_match`(exact/prefix/glob)与 `arg_value`。
    """

    tool: str = Field(min_length=1, max_length=120)
    action: Literal["auto", "confirm", "deny"]
    arg_name: str = Field(default="", max_length=64)
    arg_match: str = Field(default="", max_length=16)
    arg_value: str = Field(default="", max_length=512)

    @field_validator("tool")
    @classmethod
    def _strip_tool(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("tool 不能为空")
        return stripped

    @field_validator("arg_name", "arg_match")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _check_argument_condition(self) -> Self:
        if not self.arg_name:
            if self.arg_match or self.arg_value:
                raise ValueError("arg_name 为空时 arg_match/arg_value 必须为空")
            return self
        if self.arg_match not in VALID_MATCHES:
            raise ValueError("arg_match 只能是 exact/prefix/glob")
        if not self.arg_value:
            raise ValueError("arg_value 不能为空")
        return self


class ApprovalPolicyUpdate(BaseModel):
    """整表替换:提交的就是更新后的完整策略集。"""

    policies: list[ApprovalPolicyIn] = Field(default_factory=list)

    @field_validator("policies")
    @classmethod
    def _unique_keys(cls, value: list[ApprovalPolicyIn]) -> list[ApprovalPolicyIn]:
        keys = [
            (item.tool, item.arg_name, item.arg_match, item.arg_value) for item in value
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("同一工具名+参数条件只能出现一次")
        return value


class ApprovalPolicyRead(BaseModel):
    tool: str
    action: str
    arg_name: str = ""
    arg_match: str = ""
    arg_value: str = ""
