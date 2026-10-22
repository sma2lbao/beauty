"""knowledge-service HTTP 客户端:让模型"先查资料再回答"(§9)。

只读能力:向量检索。失败统一映射 502——由工具层回喂模型,不中断图。
测试装配 FakeKnowledge(duck typing),零网络。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from common.errors import UpstreamError
from common.middleware import inject_request_headers


class Knowledge(Protocol):
    """知识库接口:向量检索 + 文档摄取(§8)。"""

    async def search(
        self, tenant_id: str, query: str, limit: int
    ) -> dict[str, Any]: ...

    async def ingest_document(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        source: str = "manual",
    ) -> dict[str, Any]: ...

    async def list_documents(
        self, tenant_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]: ...


class KnowledgeClient:
    """knowledge-service HTTP 客户端:服务间 Bearer,租户在 token claim 里。"""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        token_provider: Callable[[str], Awaitable[str]],
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [inject_request_headers]},
        )
        self._token_provider = token_provider

    async def search(self, tenant_id: str, query: str, limit: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/knowledge/search",
            tenant_id,
            json={"query": query, "limit": limit},
            expected=200,
        )

    async def ingest_document(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        source: str = "manual",
    ) -> dict[str, Any]:
        """登记文档(摄取):失败同样映射 502,交由 outbox 退避重试。"""
        return await self._request(
            "POST",
            "/v1/knowledge/documents",
            tenant_id,
            json={"title": title, "content": content, "source": source},
            expected=201,
        )

    async def list_documents(
        self, tenant_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET", "/v1/knowledge/documents", tenant_id, expected=200
        )
        documents = payload if isinstance(payload, list) else []
        return documents[:limit]

    async def _request(
        self,
        method: str,
        path: str,
        tenant_id: str,
        *,
        json: dict[str, Any] | None = None,
        expected: int = 200,
    ) -> Any:
        try:
            headers = {
                "Authorization": f"Bearer {await self._token_provider(tenant_id)}"
            }
            response = await self._client.request(
                method, path, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "knowledge.upstream_error", f"知识库服务不可达:{exc}"
            ) from exc
        if response.status_code != expected:
            raise UpstreamError(
                "knowledge.upstream_error",
                f"知识库服务返回 {response.status_code}",
                details={"body": response.text[:200]},
            )
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
