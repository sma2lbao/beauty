"""LLM 调用的数据契约与调用链级绑定:回复/工具调用/模型偏好/回退观察者。

从 services/llm.py 拆出(门面仍由 llm.py 全量重导出):本模块只放"值对象 +
contextvar 绑定",不含任何 HTTP 细节,图节点/服务层/测试都只依赖这里的协议。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Protocol

#: 网关回退跳 message 的截断长度(见 docs/design/model-routing.md §5)
FALLBACK_MESSAGE_CHARS = 500

#: 模型路由(P1-G §6.1):调用链级模型偏好。由服务层入口(run 执行/对话)绑定,
#: GatewayClient 取模型的顺序是"调用参数 > 构造配置 > 本 contextvar"。
_model_var: ContextVar[str | None] = ContextVar("llm_model", default=None)


def bind_model(model: str | None) -> Token[str | None]:
    """绑定本次调用链的模型(返回 token,出栈时用 reset_model 还原)。"""
    return _model_var.set(model)


def reset_model(token: Token[str | None]) -> None:
    """还原调用链模型绑定(与 bind_model 成对)。"""
    _model_var.reset(token)


def current_model() -> str | None:
    """当前调用链绑定的模型;未绑定为 None(走网关默认路由)。"""
    return _model_var.get()


@dataclass(frozen=True)
class FallbackHop:
    """网关的一次模型回退跳(原模型 → 实际模型,带原因)。"""

    from_model: str
    to_model: str
    code: str = ""
    message: str = ""


FallbackObserver = Callable[[tuple[FallbackHop, ...]], Awaitable[None]]

_fallback_observer: ContextVar[FallbackObserver | None] = ContextVar(
    "llm_fallback_observer", default=None
)


def bind_fallback_observer(
    observer: FallbackObserver | None,
) -> Token[FallbackObserver | None]:
    """绑定回退观察者(run 落事件 / 对话记日志);退出时 reset。"""
    return _fallback_observer.set(observer)


def reset_fallback_observer(token: Token[FallbackObserver | None]) -> None:
    _fallback_observer.reset(token)


def parse_fallbacks(raw: Any) -> tuple[FallbackHop, ...]:
    """网关 fallbacks 数组 → FallbackHop 元组(未切换时为空)。"""
    if not isinstance(raw, list):
        return ()
    hops: list[FallbackHop] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        hops.append(
            FallbackHop(
                from_model=str(item.get("from_model") or ""),
                to_model=str(item.get("to_model") or ""),
                code=str(item.get("code") or ""),
                message=str(item.get("message") or "")[:FALLBACK_MESSAGE_CHARS],
            )
        )
    return tuple(hops)


async def notify_fallbacks(raw: Any) -> tuple[FallbackHop, ...]:
    """响应/收尾帧里的 fallbacks 交给当前观察者(无观察者时静默)。"""
    hops = parse_fallbacks(raw)
    if not hops:
        return ()
    observer = _fallback_observer.get()
    if observer is not None:
        await observer(hops)
    return hops


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
        response_format: dict[str, Any] | None = None,
    ) -> LLMReply: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]: ...

    async def stream_events(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]: ...
