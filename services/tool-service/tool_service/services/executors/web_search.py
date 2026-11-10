"""web_search 执行器:服务端出站调用搜索提供方(tavily / brave)。

与 fetch 同属"直连公网"的只读能力,因此自己守住出口边界(设计
docs/design/web-search.md §6):

- 只允许 https;提供方与 API key 由部署方配置(TOOL__SEARCH_*),模型只能
  传 query / max_results,没有模型级 URL 注入面;
- 每次请求前解析提供方主机,所有地址必须是公网单播(私网/回环/链路本地/
  CGNAT/保留地址一律拒绝);解析失败按部署错误拒绝,不 fail-open;
- 不跟随重定向(follow_redirects=False),3xx 视为失败;
- 响应体按字节上限截断读取,超限直接放弃解析(upstream_bad_response);
- 密钥只进请求头/请求体,不出现在结果、错误 hint 或日志里。

错误都是模型可见的数据(tool.* 信封里的 code),不是 API 错误。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from tool_service.services.executors.fetch import (
    FetchError,
    Resolver,
    default_resolver,
    is_public_address,
    parse_target,
)

#: 单次搜索默认超时(秒);descriptor 声明 20s,取其中较小者。
DEFAULT_SEARCH_TIMEOUT_SECONDS = 10.0
#: 响应体字节上限(超出截断读取后放弃解析)。
DEFAULT_SEARCH_MAX_BYTES = 1024 * 1024
#: 请求未指定条数时的默认值。
DEFAULT_SEARCH_DEFAULT_RESULTS = 5
#: 单次请求条数硬上限(设置值再大也按此夹取)。
DEFAULT_SEARCH_MAX_RESULTS = 10
#: 结果字段上限(设计 §4)。
MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 400
MAX_URL_CHARS = 2048
#: 错误响应体进 hint 的截断长度。
ERROR_BODY_CHARS = 200
#: 提供方默认基地址(https-only)。
PROVIDER_BASE_URLS = {
    "tavily": "https://api.tavily.com",
    "brave": "https://api.search.brave.com",
}
#: 提供方 count 上限(brave/tavily 均为 20)。
PROVIDER_MAX_RESULTS = {"tavily": 20, "brave": 20}
#: 支持的提供方(与 config / is_available 口径一致)。
SUPPORTED_PROVIDERS = frozenset(PROVIDER_BASE_URLS)
#: 无结果时的提示(空结果不是错误,设计 §4)。
EMPTY_RESULTS_NOTE = "未找到结果，可换个更短的关键词"
#: 截断提示(仅在有截断时出现,设计 §4)。
TITLE_TRUNCATED_NOTE = "标题已截断"
SNIPPET_TRUNCATED_NOTE = "摘要已截断"
#: 默认 UA(带产品标识,便于提供方识别与限流沟通)。
USER_AGENT = "beauty-moon-tool-service/0.1 (+web_search)"


class SearchError(Exception):
    """搜索失败;code 直接作为工具错误码(数据类错误,由执行器转信封)。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """按字符上限截断并补省略号;返回 (文本, 是否截断)。"""

    if len(text) <= limit:
        return text, False
    return text[: limit - 1] + "…", True


def _text(value: Any) -> str:
    """把提供方字段归一成去空白字符串(非字符串按空处理)。"""

    return value.strip() if isinstance(value, str) else ""


def _raw_results(provider: str, payload: Mapping[str, Any]) -> list[Any]:
    """按提供方取出结果数组;结构不符抛 upstream_bad_response。"""

    if provider == "brave":
        web = payload.get("web")
        if web is not None and not isinstance(web, Mapping):
            raise SearchError("tool.upstream_bad_response", "搜索提供方响应结构不符")
        raw = web.get("results") if isinstance(web, Mapping) else None
    else:
        raw = payload.get("results")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SearchError("tool.upstream_bad_response", "搜索提供方结果不是数组")
    return raw


def normalize_results(
    provider: str, payload: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    """把提供方响应归一成 {rank,title,url,snippet} 列表 + notes(设计 §4)。

    rank 按提供方顺序从 1 连续递增(不重排);title/snippet 超长截断并补
    "…",url 为空或超长时丢弃该条(无法安全引用),但保留其余结果的连续 rank。
    """

    title_truncated = False
    snippet_truncated = False
    results: list[dict[str, Any]] = []
    for item in _raw_results(provider, payload):
        if not isinstance(item, Mapping):
            continue
        url = _text(item.get("url"))
        if not url or len(url) > MAX_URL_CHARS:
            continue
        title, cut_title = _truncate(_text(item.get("title")), MAX_TITLE_CHARS)
        raw_snippet = (
            item.get("content") if provider == "tavily" else item.get("description")
        )
        snippet, cut_snippet = _truncate(_text(raw_snippet), MAX_SNIPPET_CHARS)
        title_truncated = title_truncated or cut_title
        snippet_truncated = snippet_truncated or cut_snippet
        results.append(
            {
                "rank": len(results) + 1,
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )
    if not results:
        return [], [EMPTY_RESULTS_NOTE]
    notes: list[str] = []
    if title_truncated:
        notes.append(TITLE_TRUNCATED_NOTE)
    if snippet_truncated:
        notes.append(SNIPPET_TRUNCATED_NOTE)
    return results, notes


def _body_hint(body: bytes) -> str:
    """把错误响应体压成一行短提示(截断,避免把整段响应塞给模型)。"""

    text = " ".join(body.decode("utf-8", errors="replace").split())
    return text[:ERROR_BODY_CHARS]


class SearchClient:
    """搜索提供方客户端;测试可注入 transport 与 resolver(零网络)。"""

    def __init__(
        self,
        *,
        provider: str = "tavily",
        api_key: str,
        base_url: str = "",
        timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_SEARCH_MAX_BYTES,
        default_results: int = DEFAULT_SEARCH_DEFAULT_RESULTS,
        max_results: int = DEFAULT_SEARCH_MAX_RESULTS,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"不支持的搜索提供方:{provider}")
        if not api_key:
            raise ValueError("搜索提供方缺少 API key(TOOL__SEARCH_API_KEY)")
        resolved_base = base_url.strip().rstrip("/") or PROVIDER_BASE_URLS[provider]
        try:
            scheme, host, port = parse_target(resolved_base)
        except FetchError as exc:
            raise ValueError(f"搜索提供方基地址不合法:{exc.message}") from exc
        if scheme != "https":
            raise ValueError("搜索提供方基地址必须是 https")
        self._provider = provider
        self._api_key = api_key
        self._base_url = resolved_base
        self._host = host
        self._port = port
        provider_limit = PROVIDER_MAX_RESULTS.get(provider, DEFAULT_SEARCH_MAX_RESULTS)
        self._max_results = max(1, min(int(max_results), provider_limit))
        self._default_results = max(1, min(int(default_results), self._max_results))
        self._max_bytes = max(1, int(max_bytes))
        self._timeout_seconds = float(timeout_seconds)
        self._resolver: Resolver = resolver or default_resolver
        self._client = httpx.AsyncClient(
            timeout=self._timeout_seconds,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""

        await self._client.aclose()

    async def search(
        self, query: str, *, max_results: int | None = None
    ) -> dict[str, Any]:
        """执行一次搜索;返回模型可见的成功信封(失败抛 SearchError)。"""

        limit = self._limit(max_results)
        await self._check_public()
        try:
            status, body, truncated = await self._request(query, limit)
        except httpx.TimeoutException as exc:
            raise SearchError(
                "tool.timeout",
                "搜索超时",
                hint=f"搜索超时（{self._timeout_seconds:g} 秒）",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchError(
                "tool.upstream_unavailable",
                "搜索提供方不可达",
                hint=str(exc)[:ERROR_BODY_CHARS],
                retryable=True,
            ) from exc
        self._raise_for_status(status, body)
        if truncated:
            raise SearchError(
                "tool.upstream_bad_response",
                "搜索提供方响应超过大小上限",
                hint=f"{self._provider} 响应超过 {self._max_bytes} 字节",
                retryable=True,
            )
        payload = self._decode(body, status)
        results, notes = normalize_results(self._provider, payload)
        envelope: dict[str, Any] = {
            "query": query,
            "provider": self._provider,
            "results": results,
        }
        if notes:
            envelope["notes"] = notes
        return envelope

    def _limit(self, max_results: int | None) -> int:
        """把请求条数夹到 [1, max_results](省略时用默认值)。"""

        requested = self._default_results if max_results is None else int(max_results)
        return max(1, min(requested, self._max_results))

    async def _check_public(self) -> None:
        """解析提供方主机并校验所有地址为公网单播(失败按拒绝处理)。"""

        try:
            addresses = await self._resolver(self._host, self._port)
        except Exception as exc:  # noqa: BLE001 - 解析器实现未知,一律按出口拒绝
            raise SearchError(
                "tool.search_blocked",
                "无法解析搜索提供方主机",
                hint="搜索提供方地址必须是可解析的公网地址",
            ) from exc
        if not addresses:
            raise SearchError(
                "tool.search_blocked",
                "无法解析搜索提供方主机",
                hint="搜索提供方地址必须是可解析的公网地址",
            )
        if not all(is_public_address(address) for address in addresses):
            raise SearchError(
                "tool.search_blocked",
                "拒绝访问非公网搜索提供方地址",
                hint="搜索提供方必须是公网地址",
            )

    async def _request(self, query: str, limit: int) -> tuple[int, bytes, bool]:
        """发一次请求,按字节上限截断读取;返回 (status, body, 是否截断)。"""

        if self._provider == "brave":
            url = f"{self._base_url}/res/v1/web/search"
            method = "GET"
            kwargs: dict[str, Any] = {
                "params": {"q": query, "count": limit},
                "headers": {
                    "X-Subscription-Token": self._api_key,
                    "Accept": "application/json",
                },
            }
        else:
            url = f"{self._base_url}/search"
            method = "POST"
            kwargs = {
                "json": {
                    "api_key": self._api_key,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                }
            }
        async with self._client.stream(method, url, **kwargs) as response:
            buffer = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                remaining = self._max_bytes - len(buffer)
                if remaining <= 0:
                    truncated = True
                    break
                if len(chunk) > remaining:
                    buffer += chunk[:remaining]
                    truncated = True
                    break
                buffer += chunk
            return response.status_code, bytes(buffer), truncated

    def _raise_for_status(self, status: int, body: bytes) -> None:
        """把提供方状态码映射成 SearchError(设计 §8)。"""

        if 200 <= status < 300:
            return
        hint_body = _body_hint(body)
        if status in (401, 403):
            raise SearchError(
                "tool.search_auth_failed",
                "搜索提供方鉴权失败",
                hint="搜索提供方鉴权失败，请检查 TOOL__SEARCH_API_KEY",
            )
        if status == 429:
            raise SearchError(
                "tool.upstream_rate_limited",
                "搜索提供方限流",
                hint="搜索提供方限流，稍后重试",
                retryable=True,
            )
        if status >= 500:
            raise SearchError(
                "tool.upstream_unavailable",
                f"搜索提供方返回 {status}",
                hint=hint_body,
                retryable=True,
            )
        if 300 <= status < 400:
            raise SearchError(
                "tool.upstream_bad_response",
                "搜索提供方返回了重定向",
                hint=f"{self._provider} 返回 {status}",
            )
        raise SearchError(
            "tool.upstream_unavailable",
            f"搜索提供方返回 {status}",
            hint=hint_body,
        )

    def _decode(self, body: bytes, status: int) -> dict[str, Any]:
        """解析 JSON 对象响应;非 JSON/非对象一律 upstream_bad_response。"""

        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise SearchError(
                "tool.upstream_bad_response",
                "搜索提供方响应不是合法 JSON",
                hint=f"{self._provider} 返回 {status}",
                retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise SearchError(
                "tool.upstream_bad_response",
                "搜索提供方响应不是 JSON 对象",
                hint=f"{self._provider} 返回 {status}",
                retryable=True,
            )
        return payload
