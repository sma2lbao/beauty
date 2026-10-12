"""模型调用契约:OpenAI 风格消息进,归一化响应出(内部服务不感知提供商)。

tools/tool_calls 保持 OpenAI 原生嵌套形状(``function.name/arguments``)——
上层服务回传历史消息时无需再做形状转换,litellm 直接可用。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolFunction(BaseModel):
    name: str
    arguments: str = "{}"  # JSON 字符串原文,由调用方解析


class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: ToolFunction


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str | None = Field(default=None, max_length=100000)
    tool_call_id: str | None = None  # role=tool:回指哪次调用
    tool_calls: list[ToolCall] | None = None  # role=assistant:模型请求的工具


class CompletionRequest(BaseModel):
    model: str | None = None  # 不传走 default_model(§5 路由)
    messages: list[Message] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    tools: list[dict[str, Any]] | None = None  # OpenAI tools 格式透传
    tool_choice: str | None = None


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CompletionResponse(BaseModel):
    model: str
    content: str
    tool_calls: list[ToolCall] | None = None
    usage: UsageOut | None = None
