"""模型调用契约:OpenAI 风格消息进,归一化响应出(内部服务不感知提供商)。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(max_length=100000)


class CompletionRequest(BaseModel):
    model: str | None = None  # 不传走 default_model(§5 路由)
    messages: list[Message] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CompletionResponse(BaseModel):
    model: str
    content: str
    usage: UsageOut | None = None
