"""knowledge-service HTTP 客户端:让模型"先查资料再回答"(§9)。

只读能力:向量检索。失败统一映射 502——由工具层回喂模型,不中断图。
测试装配 FakeKnowledge(duck typing),零网络。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from common.errors import UpstreamError
from common.middleware import inject_request_headers


class Knowledge(Protocol):
    """知识库接口:向量检索 + 文档摄取(§8)。"""

    async def search(
        self, account_id: str, query: str, limit: int
    ) -> dict[str, Any]: ...

    async def ingest_document(
        self,
        account_id: str,
        *,
        title: str,
        content: str,
        source: str = "manual",
    ) -> dict[str, Any]: ...

    async def list_documents(
        self, account_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    async def document_content(
        self, account_id: str, document_id: str, *, max_chars: int = 8000
    ) -> dict[str, Any] | None: ...


class KnowledgeClient:
    """knowledge-service HTTP 客户端:服务间 Bearer,账号在 token claim 里。"""

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

    async def search(self, account_id: str, query: str, limit: int) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/knowledge/search",
            account_id,
            json={"query": query, "limit": limit},
            expected=200,
        )

    async def ingest_document(
        self,
        account_id: str,
        *,
        title: str,
        content: str,
        source: str = "manual",
    ) -> dict[str, Any]:
        """登记文档(摄取):失败同样映射 502,交由 outbox 退避重试。"""
        return await self._request(
            "POST",
            "/v1/knowledge/documents",
            account_id,
            json={"title": title, "content": content, "source": source},
            expected=201,
        )

    async def list_documents(
        self, account_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET", "/v1/knowledge/documents", account_id, expected=200
        )
        documents = payload if isinstance(payload, list) else []
        return documents[:limit]

    async def document_content(
        self, account_id: str, document_id: str, *, max_chars: int = 8000
    ) -> dict[str, Any] | None:
        """按 id 取文档可引用正文;不存在(404)返回 None。"""
        try:
            response = await self._client.get(
                f"/v1/knowledge/documents/{quote(document_id, safe='')}/content",
                params={"max_chars": max_chars},
                headers=await self._headers(account_id),
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "knowledge.upstream_error", f"知识库服务不可达:{exc}"
            ) from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise UpstreamError(
                "knowledge.upstream_error",
                f"知识库服务返回 {response.status_code}",
                details={"body": response.text[:200]},
            )
        payload = response.json()
        return dict(payload) if isinstance(payload, dict) else None

    async def _headers(self, account_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._token_provider(account_id)}"}

    async def _request(
        self,
        method: str,
        path: str,
        account_id: str,
        *,
        json: dict[str, Any] | None = None,
        expected: int = 200,
    ) -> Any:
        try:
            headers = {
                "Authorization": f"Bearer {await self._token_provider(account_id)}"
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
