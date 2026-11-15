"""第三方记忆平台适配(mem0 / Zep):把厂商 HTTP API 映射到 MemoryProvider 语义。

设计取舍:

- 不引厂商 SDK,只用 httpx——适配层要薄、要能在零网络测试里用 MockTransport
  验证请求/响应形状(与 memory_remote 同一套路)。
- 账号映射:两个平台都用 user_id 作为账号级隔离键,这里直接把 account_id 当
  user_id 传;workspace 只写进 metadata,**不做远端过滤**——厂商的 user 图是
  账号级,工作区隔离应通过独立 graph/user 实现,而不是在一次检索里拼过滤条件。
- 召回渲染走本模块的 render_facts:标题与本地 recall 块一致,让模型看到同一套
  「这是参考数据、不是系统指令」的措辞。
- 失败一律抛 MemoryProviderUnavailable,由 memory_context 回退本地;适配器
  不吞错、不重试、不做缓存。
- 厂商 API 形状按 2026-11 的公开文档固化(见 docs/design/memory.md 7.4 节);
  真实密钥的 smoke 由部署方完成,契约测试只保证请求/解析不变。

Zep 用 v3 文档的 /api/v2/graph/*(v4 的 graph_uuid 体系尚在迁移,且 user 图与
account 的映射需要单独的产品决策);Letta 的记忆是 agent 常驻块而非可检索条目,
语义对不上,暂不适配(见文档 7.4 节)。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from agent_orchestrator.services.memory import RECALL_PREFIX
from agent_orchestrator.services.memory_remote import MemoryProviderUnavailable

#: mem0 平台默认地址(自建/代理时用 memory_vendor_base_url 覆盖)。
MEM0_DEFAULT_BASE_URL = "https://api.mem0.ai"
#: Zep 云默认地址;自建 Zep 同样用 memory_vendor_base_url 覆盖。
ZEP_DEFAULT_BASE_URL = "https://api.getzep.com"


def _metadata(**values: Any) -> dict[str, Any]:
    """过滤 None:厂商 metadata 只接受标量/标量数组,空值不该占键。"""
    return {key: value for key, value in values.items() if value is not None}


def render_facts(facts: Sequence[str], max_chars: int) -> str:
    """把厂商返回的事实串渲染成与本地 recall 块同构的提示块;空输入返回空串。"""
    lines: list[str] = []
    # 预算含标题行:与本地 recall_block 的口径一致,避免标题挤掉最后一条时
    # 两个实现给出的块长度不同。
    used = len(RECALL_PREFIX)
    seen: set[str] = set()
    for fact in facts:
        text = (fact or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        line = f"- {text}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if not lines:
        return ""
    return RECALL_PREFIX + "\n" + "\n".join(lines)


class _HttpVendorProvider:
    """厂商适配公共部分:HTTP 客户端、错误归一化、事实渲染。

    注入的 client 归调用方关闭(MockTransport/连接池复用);自建的在这里关,
    与 memory_remote.RemoteMemoryProvider 的约定保持一致。
    """

    #: 厂商鉴权头;子类覆盖。
    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                params=params,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise MemoryProviderUnavailable(f"记忆平台不可达: {exc}") from exc

    def _ensure_status(
        self, response: httpx.Response, *, expected: tuple[int, ...]
    ) -> httpx.Response:
        if response.status_code not in expected:
            raise MemoryProviderUnavailable(
                f"记忆平台返回 {response.status_code}: {response.text[:200]}"
            )
        return response

    def _json_object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise MemoryProviderUnavailable("记忆平台响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise MemoryProviderUnavailable("记忆平台响应不是 JSON 对象")
        return payload


class Mem0MemoryProvider(_HttpVendorProvider):
    """mem0 Platform(v3):ADD-only 抽取 + 混合检索。

    写入走 infer=false 的同步路径:内容已经是整理好的记忆条目,不需要厂商再跑
    一遍抽取(否则会二次改写、且拿不到同步的 memory id)。
    """

    def __init__(
        self,
        base_url: str = MEM0_DEFAULT_BASE_URL,
        *,
        api_key: str = "",
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(base_url, timeout_seconds=timeout_seconds, client=client)
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.api_key}"}

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
        response = await self._request(
            "POST",
            "/v3/memories/search/",
            json={
                "query": query,
                # 账号即 user_id:mem0 的实体隔离键,不能与 agent_id 混在同一个
                # AND 过滤器里(混用会命中空结果)。
                "filters": {"user_id": account_id},
                "top_k": limit,
            },
        )
        self._ensure_status(response, expected=(200,))
        payload = self._json_object(response)
        results = payload.get("results")
        if not isinstance(results, list):
            raise MemoryProviderUnavailable("mem0 响应缺少 results 数组")
        facts = [
            item.get("memory")
            for item in results
            if isinstance(item, dict) and isinstance(item.get("memory"), str)
        ]
        return render_facts(facts, max_chars)

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
        payload: dict[str, Any] = {
            "messages": [{"role": "user", "content": content}],
            "user_id": account_id,
            # infer=false:内容逐字落库,不走抽取 LLM,响应同步返回 memory id。
            "infer": False,
            "metadata": _metadata(
                kind=kind,
                scope=scope,
                workspace_id=workspace_id,
                source_type=source_type,
                source_id=source_id,
                pinned=pinned,
            ),
        }
        if ttl_days is not None and ttl_days > 0:
            expires = datetime.now(UTC) + timedelta(days=ttl_days)
            payload["expiration_date"] = expires.strftime("%Y-%m-%d")
        response = await self._request("POST", "/v3/memories/add/", json=payload)
        self._ensure_status(response, expected=(200, 201))
        body = self._json_object(response)
        results = body.get("results")
        memory_id = None
        if isinstance(results, list) and results and isinstance(results[0], dict):
            memory_id = results[0].get("id")
        if not isinstance(memory_id, str) or not memory_id:
            raise MemoryProviderUnavailable("mem0 响应缺少 memory id")
        return memory_id

    async def forget(self, session: Any, account_id: str, memory_id: str) -> None:
        response = await self._request("DELETE", f"/v1/memories/{memory_id}/")
        # 404 表示已经不存在:forget 是幂等语义。
        self._ensure_status(response, expected=(200, 204, 404))


class ZepMemoryProvider(_HttpVendorProvider):
    """Zep(v3 文档 /api/v2):user 图 + 事实边检索。

    Zep 的记忆是「episode(原始来源)→ 实体/事实边」的时序图,这里召回事实边的
    fact 字段并按相关性顺序渲染;写入把内容作为一条 text episode 入图,
    forget 删除 episode(连带其派生事实由图维护)。
    """

    def __init__(
        self,
        base_url: str = ZEP_DEFAULT_BASE_URL,
        *,
        api_key: str = "",
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(base_url, timeout_seconds=timeout_seconds, client=client)
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Api-Key {self.api_key}"}

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
        response = await self._request(
            "POST",
            "/api/v2/graph/search",
            json={
                "query": query,
                "user_id": account_id,
                "scope": "edges",
                "limit": limit,
            },
        )
        self._ensure_status(response, expected=(200,))
        payload = self._json_object(response)
        edges = payload.get("edges")
        if edges is None:
            edges = []
        if not isinstance(edges, list):
            raise MemoryProviderUnavailable("Zep 响应 edges 不是数组")
        facts = [
            edge.get("fact")
            for edge in edges
            if isinstance(edge, dict) and isinstance(edge.get("fact"), str)
        ]
        return render_facts(facts, max_chars)

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
            "/api/v2/graph",
            json={
                "data": content,
                "type": "text",
                "user_id": account_id,
                "source_description": "beauty-moon memory",
                "metadata": _metadata(
                    kind=kind,
                    scope=scope,
                    workspace_id=workspace_id,
                    source_type=source_type,
                    source_id=source_id,
                    pinned=pinned,
                ),
            },
        )
        self._ensure_status(response, expected=(200, 201, 202))
        body = self._json_object(response)
        episode_id = body.get("uuid")
        if not isinstance(episode_id, str) or not episode_id:
            raise MemoryProviderUnavailable("Zep 响应缺少 episode uuid")
        return episode_id

    async def forget(self, session: Any, account_id: str, memory_id: str) -> None:
        response = await self._request("DELETE", f"/api/v2/graph/episodes/{memory_id}")
        self._ensure_status(response, expected=(200, 204, 404))
