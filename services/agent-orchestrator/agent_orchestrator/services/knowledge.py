"""knowledge-service HTTP 客户端:让模型"先查资料再回答"(§9)。

只读能力:向量检索。失败统一映射 502——由工具层回喂模型,不中断图。
测试装配 FakeKnowledge(duck typing),零网络。
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from common.errors import UpstreamError


class Knowledge(Protocol):
    """知识库接口:向量检索。"""

    async def search(
        self, tenant_id: str, query: str, limit: int
    ) -> dict[str, Any]: ...


class KnowledgeClient:
    """knowledge-service HTTP 客户端:租户经 X-Tenant-Id 头传递。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_seconds, transport=transport
        )

    async def search(self, tenant_id: str, query: str, limit: int) -> dict[str, Any]:
        try:
            response = await self._client.post(
                "/v1/knowledge/search",
                json={"query": query, "limit": limit},
                headers={"X-Tenant-Id": tenant_id},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "knowledge.upstream_error", f"知识库服务不可达:{exc}"
            ) from exc
        if response.status_code != 200:
            raise UpstreamError(
                "knowledge.upstream_error",
                f"知识库服务返回 {response.status_code}",
                details={"body": response.text[:200]},
            )
        return dict(response.json())

    async def aclose(self) -> None:
        await self._client.aclose()
