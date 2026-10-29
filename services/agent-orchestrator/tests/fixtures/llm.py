"""LLM 替身(FakeLLM):按调用次序吐回复,零网络、零 token。

约定:LLM 与工具全部注入 fake;契约由 duck typing 满足
(见 services/llm.py 的 Protocol)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from agent_orchestrator.services.llm import (
    LLMReply,
    LLMStreamEvent,
    notify_fallbacks,
)


class FakeLLM:
    """按调用次序吐回复:字符串自动包成 LLMReply(content=...)。"""

    def __init__(
        self,
        responses: list[str | LLMReply],
        fail_on_call: int | None = None,
        *,
        chunks: dict[int, list[str]] | None = None,
        fallbacks: list[dict[str, Any]] | None = None,
    ):
        self.responses = list(responses)
        self.fail_on_call = fail_on_call
        self.calls: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]] | None] = []
        # 每次调用显式传入的 model / response_format(断言调用契约用)
        self.models: list[str | None] = []
        self.response_formats: list[dict[str, Any] | None] = []
        # 模拟网关回退:非空时每次调用都触发一次 fallback 观察者
        self.fallbacks = list(fallbacks or [])
        # 调用序号(1 起)→ 正文块;指定后流式按这些多字符块吐出(验证不重切)
        self.chunks = dict(chunks or {})

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMReply:
        self.calls.append(messages)
        self.tools_seen.append(tools)
        self.models.append(model)
        self.response_formats.append(response_format)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("模型挂了")
        if self.fallbacks:
            await notify_fallbacks(self.fallbacks)
        response = self.responses[len(self.calls) - 1]
        return (
            response if isinstance(response, LLMReply) else LLMReply(content=response)
        )

    async def stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """流式(结构化):先吐正文块,有工具调用再吐一个工具事件。

        内部仍走 complete(),所以调用序号与失败注入(fail_on_call)与旧路径一致;
        默认按字切分,``chunks`` 可按调用序号指定多字符块——用于断言服务端
        原样转发、不重新按字切分。
        """
        reply = await self.complete(
            messages,
            model=model,
            tools=tools,
            response_format=response_format,
        )
        chunks = self.chunks.get(len(self.calls))
        for piece in chunks if chunks is not None else list(reply.content):
            yield LLMStreamEvent(text=piece)
        if reply.tool_calls:
            yield LLMStreamEvent(tool_calls=reply.tool_calls)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """流式(只要正文):委托 stream_events,保持调用序号/失败注入一致。"""
        async for event in self.stream_events(
            messages, model=model, tools=tools, response_format=response_format
        ):
            if event.text:
                yield event.text
