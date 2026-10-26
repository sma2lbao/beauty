"""LLM 客户端:orchestrator 的所有模型调用都经 llm-gateway(架构 §5 唯一出口)。

注入边界(架构 §4):graph 节点只依赖 LLMClient 协议——生产装配
GatewayClient(HTTP),测试装配 FakeLLM,零网络零 token。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from common.errors import UpstreamError
from common.logging import current_context, get_logger
from common.middleware import inject_request_headers
from common.security import SECRET_HEADER, TENANT_HEADER

from agent_orchestrator import metrics
from agent_orchestrator.services import usage as usage_service

# 可重试状态:P0-7 语义是"上游临时故障",429 限流 + 5xx 网关/上游故障
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class ToolCall:
    """模型请求的一次工具调用(arguments 为 JSON 字符串原文)。"""

    id: str
    name: str
    arguments: str = "{}"


def _upstream_error(
    code: str, message: str, *, status: int | None = None
) -> UpstreamError:
    """构造上游错误并计数(P2 指标:按 provider/code 统计网关失败)。"""
    metrics.observe_llm_error(provider="llm-gateway", code=code)
    details = {"status": status} if status is not None else None
    return UpstreamError(code, message, details=details)


@dataclass(frozen=True)
class LLMReply:
    """模型回复:正文与(可选)工具调用;二者可同时存在。"""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class LLMStreamEvent:
    """流式事件:正文增量,或收尾片里的工具调用(二者不同时出现)。

    网关契约:正文 delta 帧只带 delta;工具调用只出现在收尾 done 帧,
    所以工具调用一定在所有正文增量之后才作为一个事件出现。
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class LLMClient(Protocol):
    """模型调用接口:OpenAI 风格消息进,回复(正文/工具调用)出。

    三种取法共用同一套消息形状:complete 一次拿全文(节点执行用);
    stream 逐段拿增量(对话"边输出边显示"用);stream_events 是流式的
    结构化版本——正文增量之外还能拿到收尾片里的工具调用(工具轮用)。
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

    async def stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]: ...


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


def _parse_usage(raw: Any, model: str | None) -> usage_service.UsageSample | None:
    """网关 usage 三件套 → 用量样本(P0-1:计入 usage_events 的原始数据)。"""
    if not isinstance(raw, dict):
        return None
    try:
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        total = int(raw.get("total_tokens") or (prompt + completion))
    except (TypeError, ValueError):
        return None
    if prompt == 0 and completion == 0:
        return None
    return usage_service.UsageSample(
        model=str(model or "unknown"),
        prompt_tokens=max(prompt, 0),
        completion_tokens=max(completion, 0),
        total_tokens=max(total, 0),
    )


def _identity_headers_hook(
    internal_secret: str,
) -> Callable[[httpx.Request], Awaitable[None]]:
    """出站钩子:先透传 request_id/traceparent,再补租户与内部密钥。

    llm-gateway 按租户解析 BYOK 密钥,依赖 X-Tenant-Id + X-Internal-Secret 两个头;
    租户取当前 contextvars(请求/job 边界由 api/deps 与 jobs_worker 绑定),内部密钥取
    本服务配置。两个头都用 setdefault:上游显式给了头时不覆盖。
    """

    async def _hook(request: httpx.Request) -> None:
        await inject_request_headers(request)
        tenant_id = current_context().get("tenant_id")
        if tenant_id:
            request.headers.setdefault(TENANT_HEADER, str(tenant_id))
        if internal_secret:
            request.headers.setdefault(SECRET_HEADER, internal_secret)

    return _hook


class GatewayClient:
    """llm-gateway HTTP 客户端:任何失败统一映射 UpstreamError(502)。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 60.0,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 1.5,
        internal_secret: str = "",
    ) -> None:
        self.model = model  # None:走网关默认路由
        self.retry_attempts = max(0, retry_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        # 网关 BYOK:租户头 + 内部密钥头(密钥留空时只带租户,走平台默认密钥)
        self.internal_secret = internal_secret
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [_identity_headers_hook(internal_secret)]},
        )

    async def _backoff(
        self, log: Any, attempt: int, code: str, status: int | None
    ) -> None:
        """记录一次重试并按指数退避(1.5s、3s、6s…)。"""
        log.warning(
            "llm_retry",
            attempt=attempt + 1,
            max_attempts=self.retry_attempts,
            code=code,
            status=status,
        )
        await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))

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
        attempt = 0
        while True:
            try:
                resp = await self._client.post("/v1/completions", json=payload)
            except httpx.TransportError as exc:
                # 连接/超时类错误可重试;最终失败统一映射 502
                if attempt >= self.retry_attempts:
                    raise _upstream_error(
                        "llm.upstream_error", f"模型网关不可达:{exc}"
                    ) from exc
                await self._backoff(log, attempt, "llm.upstream_error", None)
                attempt += 1
                continue
            if resp.status_code != 200:
                detail = _error_detail(resp)
                if (
                    resp.status_code in _RETRYABLE_STATUSES
                    and attempt < self.retry_attempts
                ):
                    await self._backoff(log, attempt, detail["code"], resp.status_code)
                    attempt += 1
                    continue
                raise _upstream_error(
                    detail["code"],
                    detail["message"],
                    status=resp.status_code,
                )
            break
        data = resp.json()
        calls = _parse_tool_calls(data.get("tool_calls"))
        # P0-1 用量闭环:网关回传的 usage 记进当前采集器(无采集器时 no-op)
        usage_service.record(
            _parse_usage(data.get("usage"), data.get("model") or chosen)
        )
        log.info(
            "llm_call",
            model=chosen,
            prompt_chars=sum(len(str(m.get("content") or "")) for m in messages),
            tool_calls=len(calls),
            retries=attempt,
        )
        return LLMReply(content=data.get("content") or "", tool_calls=calls)

    async def stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """流式调用:逐段产出正文增量,收尾片里的工具调用单独成一个事件。

        网关契约(POST /v1/completions/stream):正文帧 {"delta": "..."};
        收尾帧 {"done": true, "content": "<全文>", "tool_calls": [...]}(无工具时
        省略该键),工具调用**只在** done 片出现,所以工具轮可以边收正文边执行。

        流一旦开始就无法改状态码,所以失败分两处:开流前(非 200)直接抛;
        流中途(error 片 / 连接断)在产出若干增量后抛——调用方自行决定
        已产出的部分怎么处理(对话侧会把它落库,不丢半截回复)。

        重试只在"开流前":一旦已经吐出增量,重发会拼出重复正文,故绝不重试。
        """
        chosen = model or self.model
        log = get_logger("agent_orchestrator.llm")
        payload: dict[str, Any] = {"model": chosen, "messages": messages}
        if tools:
            payload["tools"] = tools
        chunks: list[str] = []
        calls: tuple[ToolCall, ...] = ()
        usage_sample: usage_service.UsageSample | None = None
        attempt = 0
        while True:
            started = False  # 本轮是否已吐出增量
            retry_after: tuple[str, int | None] | None = None
            try:
                async with self._client.stream(
                    "POST", "/v1/completions/stream", json=payload
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        detail = _error_detail(resp)
                        if (
                            resp.status_code in _RETRYABLE_STATUSES
                            and attempt < self.retry_attempts
                        ):
                            retry_after = (detail["code"], resp.status_code)
                        else:
                            raise _upstream_error(
                                detail["code"],
                                detail["message"],
                                status=resp.status_code,
                            )
                    else:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                data = json.loads(line[6:])
                            except ValueError:
                                continue
                            error = data.get("error")
                            if error:
                                raise _upstream_error(
                                    str(error.get("code") or "llm.upstream_error"),
                                    str(error.get("message") or "模型流式调用失败"),
                                )
                            if data.get("done"):
                                # done 片:usage 与 tool_calls 都只在这里出现一次
                                if data.get("usage"):
                                    usage_sample = _parse_usage(
                                        data.get("usage"), data.get("model") or chosen
                                    )
                                calls = _parse_tool_calls(data.get("tool_calls"))
                            delta = data.get("delta")
                            if isinstance(delta, str) and delta:
                                started = True
                                chunks.append(delta)
                                yield LLMStreamEvent(text=delta)
            except httpx.TransportError as exc:
                if started or attempt >= self.retry_attempts:
                    raise _upstream_error(
                        "llm.upstream_error", f"模型网关不可达:{exc}"
                    ) from exc
                retry_after = ("llm.upstream_error", None)
            if retry_after is None:
                break
            await self._backoff(log, attempt, *retry_after)
            attempt += 1
        usage_service.record(usage_sample)
        log.info(
            "llm_stream",
            model=chosen,
            prompt_chars=sum(len(str(m.get("content") or "")) for m in messages),
            chars=len("".join(chunks)),
            tool_calls=len(calls),
            retries=attempt,
        )
        if calls:
            # 工具调用在所有正文增量之后才下发:调用方据此决定"这一轮是工具轮"
            yield LLMStreamEvent(tool_calls=calls)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """流式调用(只要正文):stream_events 的薄包装,现有调用方零改动。"""
        async for event in self.stream_events(messages, model=model, tools=tools):
            if event.text:
                yield event.text

    async def aclose(self) -> None:
        await self._client.aclose()
