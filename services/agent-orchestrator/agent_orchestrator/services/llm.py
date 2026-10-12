"""LLM 客户端:orchestrator 的所有模型调用都经 llm-gateway(架构 §5 唯一出口)。

注入边界(架构 §4):graph 节点只依赖 LLMClient 协议——生产装配
GatewayClient(HTTP),测试装配 FakeLLM,零网络零 token。
"""

from __future__ import annotations

from typing import Protocol

import httpx
from common.errors import UpstreamError
from common.logging import get_logger


class LLMClient(Protocol):
    """模型调用接口:OpenAI 风格消息进,正文出。"""

    async def complete(
        self, messages: list[dict[str, str]], *, model: str | None = None
    ) -> str: ...


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
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    async def complete(
        self, messages: list[dict[str, str]], *, model: str | None = None
    ) -> str:
        chosen = model or self.model
        log = get_logger("agent_orchestrator.llm")
        try:
            resp = await self._client.post(
                "/v1/completions", json={"model": chosen, "messages": messages}
            )
        except httpx.HTTPError as exc:
            raise UpstreamError("llm.upstream_error", f"模型网关不可达:{exc}") from exc
        if resp.status_code != 200:
            detail = _error_detail(resp)
            raise UpstreamError(
                detail["code"],
                detail["message"],
                details={"status": resp.status_code},
            )
        log.info(
            "llm_call",
            model=chosen,
            prompt_chars=sum(len(m.get("content", "")) for m in messages),
        )
        return resp.json()["content"]

    async def aclose(self) -> None:
        await self._client.aclose()
