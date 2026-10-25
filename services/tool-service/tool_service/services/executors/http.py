"""HTTP 执行器:把只读工具代理到下游服务,或直接抓取公网文本。

工具执行分两类:文件类工具经沙箱(workspace-service)执行;知识库检索这类
跨服务只读能力没有沙箱语义,由本模块用服务间 token(TokenBroker,
subject=svc-tool)带租户 JWT 调下游 HTTP API。`fetch` 属于第三种:不代理
任何下游服务,由本模块直连公网(SSRF 防护与大小上限见
tool_service/services/executors/fetch.py)。两类都把响应/失败归一成统一工具
信封。HTTP 执行器只做只读动作:参数在 journal 层已校验,这里不做写操作、
不建表、不发事件(设计 §8.2)。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from common.errors import UpstreamError
from common.middleware import inject_request_headers
from common.security.token_broker import TokenBroker
from common.tools import ToolError, ToolExecuteResponse

from tool_service.core.config import Settings
from tool_service.services.executors.fetch import FetchClient, FetchError
from tool_service.services.sandbox import SERVICE_SUBJECT

#: 知识库检索路径(对应 knowledge-service 的 POST /v1/knowledge/search)。
KNOWLEDGE_SEARCH_PATH = "/v1/knowledge/search"
#: 检索默认条数(与旧 orchestrator 工具的默认值一致)。
DEFAULT_KNOWLEDGE_LIMIT = 5
#: 下游错误响应体截断长度(只进 hint,避免把整段响应塞给模型)。
ERROR_BODY_LIMIT = 200
#: httpx 客户端默认超时;实际单次调用以 descriptor.timeout_seconds 为准。
DEFAULT_HTTP_TIMEOUT_SECONDS = 20.0
#: 内部密钥缺失属于配置问题,重试无意义。
_AUTH_NOT_RETRYABLE = "auth.internal_secret_missing"

TokenProvider = Callable[[str], Awaitable[str]]


class UpstreamStatusError(Exception):
    """下游返回非 2xx:保留状态码与截断响应体,供执行器做错误映射。"""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"upstream status {status_code}")
        self.status_code = status_code
        self.body = body


def _normalize_search(payload: dict[str, Any]) -> dict[str, Any]:
    """把知识库响应归一成旧工具形状(模型侧观感不变)。"""

    raw_hits = payload.get("hits")
    hits: list[dict[str, Any]] = []
    if isinstance(raw_hits, list):
        for item in raw_hits:
            if not isinstance(item, dict):
                continue
            hits.append(
                {
                    "title": str(item.get("title") or ""),
                    "score": item.get("score"),
                    "content": str(item.get("content") or ""),
                }
            )
    return {
        "query": str(payload.get("query") or ""),
        "provider": str(payload.get("provider") or ""),
        "model": str(payload.get("model") or ""),
        "hits": hits,
    }


class KnowledgeClient:
    """knowledge-service 检索客户端(测试可注入 httpx transport)。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        token_provider: TokenProvider,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            event_hooks={"request": [inject_request_headers]},
        )
        self._token_provider = token_provider

    async def search(self, tenant_id: str, *, query: str, limit: int) -> dict[str, Any]:
        """检索知识库;传输层错误原样抛出(由执行器映射),非 200 抛状态错误。"""

        token = await self._token_provider(tenant_id)
        response = await self._client.post(
            KNOWLEDGE_SEARCH_PATH,
            json={"query": query, "limit": limit},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code != 200:
            raise UpstreamStatusError(
                response.status_code, response.text[:ERROR_BODY_LIMIT]
            )
        return _normalize_search(self._decode(response))

    def _decode(self, response: httpx.Response) -> dict[str, Any]:
        """解析 JSON 对象响应;非法响应包 UpstreamError(不裸抛 ValueError)。"""

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(
                "knowledge.bad_response",
                "知识库响应不是合法 JSON",
                details={"status": response.status_code},
            ) from exc
        if not isinstance(payload, dict):
            raise UpstreamError(
                "knowledge.bad_response",
                "知识库响应不是 JSON 对象",
                details={"status": response.status_code},
            )
        return dict(payload)

    async def aclose(self) -> None:
        await self._client.aclose()


def _error(
    code: str,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
) -> ToolExecuteResponse:
    """构造失败工具信封。"""

    return ToolExecuteResponse(
        ok=False,
        error=ToolError(
            code=code,
            message=message,
            hint=hint[:ERROR_BODY_LIMIT],
            retryable=retryable,
        ),
    )


def _status_error(exc: UpstreamStatusError) -> ToolExecuteResponse:
    """把下游状态码映射成工具错误码(设计 §8.2)。"""

    detail = f"知识库服务返回 {exc.status_code}:{exc.body}"
    if exc.status_code == 404:
        return _error("tool.not_found", "知识库未找到目标资源", hint=detail)
    if 400 <= exc.status_code < 500:
        return _error("tool.invalid_args", "知识库拒绝了检索参数", hint=detail)
    return _error(
        "tool.upstream_unavailable",
        f"知识库服务返回 {exc.status_code}",
        hint=detail,
        retryable=True,
    )


class HttpToolExecutor:
    """HTTP 工具执行器:只做只读动作,返回统一工具信封。"""

    def __init__(
        self,
        *,
        knowledge: KnowledgeClient | None = None,
        fetcher: FetchClient | None = None,
    ) -> None:
        self._knowledge = knowledge
        self._fetcher = fetcher

    async def execute(
        self, tenant_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolExecuteResponse:
        """执行一个 HTTP 工具;未知工具明确报不可用(不猜实现)。"""

        if tool_name == "search_knowledge":
            return await self._search_knowledge(tenant_id, arguments)
        if tool_name == "fetch":
            return await self._fetch(arguments)
        return _error(
            "tool.executor_unavailable",
            f"http 执行器不支持工具 {tool_name}",
        )

    async def _search_knowledge(
        self, tenant_id: str, arguments: dict[str, Any]
    ) -> ToolExecuteResponse:
        if self._knowledge is None:
            return _error("tool.executor_unavailable", "知识库客户端未装配")
        query = str(arguments.get("query") or "")
        raw_limit = arguments.get("limit")
        limit = DEFAULT_KNOWLEDGE_LIMIT if raw_limit is None else int(raw_limit)
        try:
            result = await self._knowledge.search(tenant_id, query=query, limit=limit)
        except UpstreamStatusError as exc:
            return _status_error(exc)
        except httpx.TimeoutException:
            return _error("tool.timeout", "知识库检索超时", retryable=True)
        except httpx.HTTPError as exc:
            return _error(
                "tool.upstream_unavailable",
                "知识库服务不可达",
                hint=str(exc),
                retryable=True,
            )
        except UpstreamError as exc:
            return _error(
                "tool.upstream_unavailable",
                str(exc),
                hint=str(exc.details.get("body", "")),
                retryable=exc.code != _AUTH_NOT_RETRYABLE,
            )
        return ToolExecuteResponse(ok=True, result=result)

    async def _fetch(self, arguments: dict[str, Any]) -> ToolExecuteResponse:
        """抓取公网文本;未装配抓取客户端说明服务端没开 fetch。"""

        if self._fetcher is None:
            return _error(
                "tool.executor_unavailable",
                "fetch 未启用",
                hint="服务端未开启 TOOL__FETCH_ENABLED",
            )
        url = str(arguments.get("url") or "")
        try:
            result = await self._fetcher.get(url)
        except FetchError as exc:
            return _error(
                exc.code,
                exc.message,
                hint=exc.hint,
                retryable=exc.retryable,
            )
        except httpx.TimeoutException:
            return _error("tool.timeout", "抓取超时", retryable=True)
        except httpx.HTTPError as exc:
            return _error(
                "tool.fetch_failed",
                "抓取失败",
                hint=str(exc),
                retryable=True,
            )
        return ToolExecuteResponse(ok=True, result=result)


class KnowledgeRuntime:
    """持有 TokenBroker + KnowledgeClient 的运行时,供 FastAPI lifespan 管理。"""

    def __init__(self, *, settings: Settings) -> None:
        self._broker = TokenBroker(
            base_url=settings.auth_issuer,
            internal_secret=settings.internal_secret,
            subject=SERVICE_SUBJECT,
        )
        self.client = KnowledgeClient(
            settings.knowledge_service_url,
            timeout_seconds=settings.knowledge_timeout_seconds,
            token_provider=self._broker.token,
        )

    async def aclose(self) -> None:
        """先关 HTTP 客户端再关 broker(与沙箱运行时同序)。"""

        await self.client.aclose()
        await self._broker.aclose()
