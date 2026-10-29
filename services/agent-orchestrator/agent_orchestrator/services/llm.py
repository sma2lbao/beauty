"""LLM 客户端门面:orchestrator 的所有模型调用都经 llm-gateway(架构 §5 唯一出口)。

注入边界(架构 §4):graph 节点只依赖 LLMClient 协议——生产装配
GatewayClient(HTTP),测试装配 FakeLLM,零网络零 token。

实现已拆分到两个兄弟模块,本模块只做门面(保持既有 import 路径):
- llm_models:值对象/协议/调用链级模型与回退观察者绑定;
- llm_gateway:GatewayClient 与 HTTP 重试/用量/错误映射。
"""

from __future__ import annotations

from common.errors import UpstreamError

from agent_orchestrator.services.llm_gateway import GatewayClient
from agent_orchestrator.services.llm_models import (
    FALLBACK_MESSAGE_CHARS,
    FallbackHop,
    FallbackObserver,
    LLMClient,
    LLMReply,
    LLMStreamEvent,
    ToolCall,
    bind_fallback_observer,
    bind_model,
    current_model,
    notify_fallbacks,
    parse_fallbacks,
    reset_fallback_observer,
    reset_model,
)

__all__ = [
    "FALLBACK_MESSAGE_CHARS",
    "GatewayClient",
    "LLMClient",
    "LLMReply",
    "LLMStreamEvent",
    "FallbackHop",
    "FallbackObserver",
    "ToolCall",
    "UpstreamError",
    "bind_fallback_observer",
    "bind_model",
    "current_model",
    "notify_fallbacks",
    "parse_fallbacks",
    "reset_fallback_observer",
    "reset_model",
]
