"""远程记忆提供方(P3-C):把记忆能力外接成 HTTP 服务时的客户端实现。

契约(与本地语义对齐,便于后续接 mem0/Zep 的适配层或独立记忆服务):

- ``POST {base}/v1/memories/recall`` {"query","workspace_id","limit","max_chars"}
  → {"block": "已渲染好的提示块"} (空字符串表示没有命中,而不是 404);
- ``POST {base}/v1/memories/remember`` {"content","kind","scope",...}
  → {"id": "记忆 id"};
- ``DELETE {base}/v1/memories/{id}`` → 204(幂等,404 也按成功处理)。

失败一律抛 ``MemoryProviderUnavailable``:调用方决定降级(注入路径回退本地),
provider 自身不吞错、不重试——记忆是增强项,不该让一次抖动拖慢整个请求。
"""

from __future__ import annotations

from typing import Any

import httpx
from common.logging import current_context
from common.security import ACCOUNT_HEADER, SECRET_HEADER


class MemoryProviderUnavailable(Exception):
    """远程记忆提供方不可用(网络/非 2xx/响应不合约);调用方决定是否回退。"""


class RemoteMemoryProvider:
    """HTTP 记忆提供方:请求体按记忆服务的独立契约,不暴露本地表结构。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
        internal_secret: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # 注入的 client 归调用方关闭(MockTransport/连接池复用);自建的在这里关。
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None
        self.internal_secret = internal_secret

    def _identity_headers(self) -> dict[str, str]:
        """BYOK 身份头:账号 + 内部密钥(密钥留空只带账号)。"""
        headers: dict[str, str] = {}
        account_id = current_context().get("account_id")
        if account_id:
            headers[ACCOUNT_HEADER] = str(account_id)
        if self.internal_secret:
            headers[SECRET_HEADER] = self.internal_secret
        return headers

    async def aclose(self) -> None:
        """关闭自建的 HTTP 客户端;注入的 client 不越权关闭。"""
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                headers=self._identity_headers(),
            )
        except httpx.HTTPError as exc:
            raise MemoryProviderUnavailable(f"记忆服务不可达: {exc}") from exc
        return response

    async def recall(
        self,
        session: Any,
        account_id: str,
        query: str,
        *,
        workspace_id: str | None = None,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> str:
        """召回提示块;session 参数保留是为了与本地实现同签名(远程不用)。"""
        response = await self._request(
            "POST",
            "/v1/memories/recall",
            json={
                "query": query,
                "workspace_id": workspace_id,
                "limit": limit,
                "max_chars": max_chars,
            },
        )
        if response.status_code != 200:
            raise MemoryProviderUnavailable(
                f"记忆服务返回 {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MemoryProviderUnavailable("记忆服务响应不是合法 JSON") from exc
        block = payload.get("block") if isinstance(payload, dict) else None
        if not isinstance(block, str):
            raise MemoryProviderUnavailable("记忆服务响应缺少 block 字符串")
        return block

    async def remember(
        self,
        session: Any,
        account_id: str,
        content: str,
        *,
        kind: str = "note",
        scope: str = "account",
        workspace_id: str | None = None,
        source_type: str = "manual",
        source_id: str | None = None,
        pinned: bool = False,
        ttl_days: int | None = None,
    ) -> str:
        response = await self._request(
            "POST",
            "/v1/memories/remember",
            json={
                "content": content,
                "kind": kind,
                "scope": scope,
                "workspace_id": workspace_id,
                "source_type": source_type,
                "source_id": source_id,
                "pinned": pinned,
                "ttl_days": ttl_days,
            },
        )
        if response.status_code not in (200, 201):
            raise MemoryProviderUnavailable(
                f"记忆服务返回 {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MemoryProviderUnavailable("记忆服务响应不是合法 JSON") from exc
        memory_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(memory_id, str) or not memory_id:
            raise MemoryProviderUnavailable("记忆服务响应缺少 id")
        return memory_id

    async def forget(self, session: Any, account_id: str, memory_id: str) -> None:
        response = await self._request("DELETE", f"/v1/memories/{memory_id}")
        # 404 视为已经不存在:forget 是幂等语义,没必要让调用方分辨。
        if response.status_code not in (200, 204, 404):
            raise MemoryProviderUnavailable(
                f"记忆服务返回 {response.status_code}: {response.text[:200]}"
            )
