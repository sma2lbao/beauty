"""LLM 客户端:orchestrator 的所有模型调用都经 llm-gateway(架构 §5 唯一出口)。

注入边界(架构 §4):graph 节点只依赖 LLMClient 协议——生产装配
GatewayClient(HTTP),测试装配 FakeLLM,零网络零 token。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from common.errors import UpstreamError
from common.logging import get_logger
from common.middleware import inject_request_headers


@dataclass(frozen=True)
class ToolCall:
    """模型请求的一次工具调用(arguments 为 JSON 字符串原文)。"""

    id: str
    name: str
    arguments: str = "{}"


@dataclass(frozen=True)
class LLMReply:
    """模型回复:正文与(可选)工具调用;二者可同时存在。"""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()


class LLMClient(Protocol):
    """模型调用接口:OpenAI 风格消息进,回复(正文/工具调用)出。

    两种取法共用同一套消息形状:complete 一次拿全文(节点执行用),
    stream 逐段拿增量(对话"边输出边显示"用)。
    """

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMReply: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]: ...


def _error_detail(resp: httpx.Response) -> dict[str, str]:
    """网关错误也是统一契约 {"code","message"}(架构 §11),能解析则透传。"""
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return {
        "code": data.get("code") or "llm.upstream_error",
        "message": data.get("message") or f"模型网关返回 {resp.status_code}",
    }


def _parse_tool_calls(payload: list[dict[str, Any]] | None) -> tuple[ToolCall, ...]:
    """网关的嵌套 tool_calls → 扁平 ToolCall(调用方用起来更顺手)。"""
    calls: list[ToolCall] = []
    for raw in payload or []:
        function = raw.get("function") or {}
        calls.append(
            ToolCall(
                id=str(raw.get("id") or ""),
                name=str(function.get("name") or ""),
                arguments=str(function.get("arguments") or "{}"),
            )
        )
    return tuple(calls)


class GatewayClient:
    """llm-gateway HTTP 客户端:任何失败统一映射 UpstreamError(502)。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 60.0,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model  # None:走网关默认路由
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [inject_request_headers]},
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMReply:
        chosen = model or self.model
        log = get_logger("agent_orchestrator.llm")
        payload: dict[str, Any] = {"model": chosen, "messages": messages}
        if tools:
            payload["tools"] = tools
        try:
            resp = await self._client.post("/v1/completions", json=payload)
        except httpx.HTTPError as exc:
            raise UpstreamError("llm.upstream_error", f"模型网关不可达:{exc}") from exc
        if resp.status_code != 200:
            detail = _error_detail(resp)
            raise UpstreamError(
                detail["code"],
                detail["message"],
                details={"status": resp.status_code},
            )
        data = resp.json()
        calls = _parse_tool_calls(data.get("tool_calls"))
        log.info(
            "llm_call",
            model=chosen,
            prompt_chars=sum(len(str(m.get("content") or "")) for m in messages),
            tool_calls=len(calls),
        )
        return LLMReply(content=data.get("content") or "", tool_calls=calls)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """流式调用:逐段产出正文增量;网关的 error 片转 UpstreamError。

        流一旦开始就无法改状态码,所以失败分两处:开流前(非 200)直接抛;
        流中途(error 片 / 连接断)在产出若干增量后抛——调用方自行决定
        已产出的部分怎么处理(对话侧会把它落库,不丢半截回复)。
        """
        chosen = model or self.model
        log = get_logger("agent_orchestrator.llm")
        payload: dict[str, Any] = {"model": chosen, "messages": messages}
        if tools:
            payload["tools"] = tools
        chunks: list[str] = []
        async with self._client.stream(
            "POST", "/v1/completions/stream", json=payload
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                detail = _error_detail(resp)
                raise UpstreamError(
                    detail["code"],
                    detail["message"],
                    details={"status": resp.status_code},
                )
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except ValueError:
                    continue
                error = data.get("error")
                if error:
                    raise UpstreamError(
                        str(error.get("code") or "llm.upstream_error"),
                        str(error.get("message") or "模型流式调用失败"),
                    )
                delta = data.get("delta")
                if isinstance(delta, str) and delta:
                    chunks.append(delta)
                    yield delta
        log.info(
            "llm_stream",
            model=chosen,
            prompt_chars=sum(len(str(m.get("content") or "")) for m in messages),
            chars=len("".join(chunks)),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
