"""应用级 hooks 契约(docs/design/hooks.md §3/§4/§7)。

读端**永不返回 secret**——它只在创建与轮换响应里出现一次;写端只允许局部更新
matcher/target/timeout_seconds/enabled,事件不可改(要换事件就新建一条)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 五个事件(API 校验枚举;执行侧常量见 graph/ports.py 与 services/hooks.py)。
HookEvent = Literal[
    "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd", "PreCompact"
]

MATCHER_MAX_LENGTH = 200
TARGET_MAX_LENGTH = 1024


class HookRead(BaseModel):
    """hook 的读端形态(secret 永不回显)。"""

    id: str
    event: str
    matcher: str
    kind: str
    target: str
    timeout_seconds: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class HookSecretRead(HookRead):
    """创建/轮换的响应:额外一次性返回 secret(接收端据此校验签名)。"""

    secret: str


class HookCreate(BaseModel):
    """创建:matcher 只对工具事件生效,其余事件忽略。"""

    model_config = ConfigDict(extra="forbid")

    event: HookEvent
    matcher: str = Field(default="*", max_length=MATCHER_MAX_LENGTH)
    target: str = Field(min_length=1, max_length=TARGET_MAX_LENGTH)
    timeout_seconds: int = Field(default=3, ge=1, le=30)
    enabled: bool = True


class HookUpdate(BaseModel):
    """局部更新:省略=保持。event/kind/secret 不在此列(secret 走 /rotate)。"""

    model_config = ConfigDict(extra="forbid")

    matcher: str | None = Field(default=None, max_length=MATCHER_MAX_LENGTH)
    target: str | None = Field(default=None, min_length=1, max_length=TARGET_MAX_LENGTH)
    timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    enabled: bool | None = None
