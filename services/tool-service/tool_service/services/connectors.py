"""连接器工具面:目录(TTL 缓存)+ 执行转发(C2.4)。

目录从 connector-service 的内部端点拉取(仅「有 active+enabled 连接」的
provider,见设计 §5.3/§5.6),执行则 POST /v1/connectors/{provider}/execute。
所有调用都带 X-Internal-Secret + X-Account-Id;目录拉取失败一律降级为空,
不阻塞内置工具。审计唯一落点仍是 tool_calls(设计 D12:connector-service
不建第二张审计表)。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from common.logging import get_logger
from common.security.internal_secret import ACCOUNT_HEADER, SECRET_HEADER
from common.tools import (
    ExecutorKind,
    ToolDescriptor,
    ToolError,
    ToolExecuteResponse,
)

#: 内部目录端点(connector-service,不经网关)
CATALOG_PATH = "/v1/internal/connectors/tools"
#: 执行端点(provider 由 descriptor.connector["provider"] 决定)
EXECUTE_PATH = "/v1/connectors/{provider}/execute"
#: 连接器 HTTP 调用默认超时(秒);执行期还会被 ToolRunner 的整体超时夹住
DEFAULT_TIMEOUT_SECONDS = 10.0
ERROR_BODY_LIMIT = 200
EMPTY_TOOLS: tuple[ToolDescriptor, ...] = ()

log = get_logger("tool.connectors")


@dataclass(frozen=True, slots=True)
class ConnectorExecution:
    """一次连接器执行的返回:工具信封 + 连接器侧解析出的 connection_id。

    connection_id 只用于 tool_calls 审计,不得出现在模型可见的 result 里——
    gateway 在构造 ToolExecuteResponse 前已把它从响应 body 中剥离(设计 C2.4:
    「connection_id 写入响应便于审计」,审计唯一落点仍是 tool_calls)。
    """

    response: ToolExecuteResponse
    connection_id: str | None = None


def _error(
    code: str,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
) -> ToolExecuteResponse:
    """构造失败工具信封(与 HTTP 执行器同口径)。"""

    return ToolExecuteResponse(
        ok=False,
        error=ToolError(
            code=code,
            message=message,
            hint=hint[:ERROR_BODY_LIMIT],
            retryable=retryable,
        ),
    )


def _provider_of(descriptor: ToolDescriptor) -> str | None:
    """从 descriptor.connector 取 provider;缺失或非法返回 None。"""

    connector = descriptor.connector
    if not isinstance(connector, dict):
        return None
    provider = connector.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        return None
    return provider.strip()


def _connection_id_of(body: dict[str, Any]) -> str | None:
    """取 execute 响应顶层的 connection_id;缺失或非字符串一律视为 None。"""

    value = body.get("connection_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _strip_connection_id(body: dict[str, Any]) -> dict[str, Any]:
    """剥离顶层 connection_id,避免它被 from_payload 塞进模型可见的 result。"""

    if "connection_id" not in body:
        return body
    cleaned = dict(body)
    cleaned.pop("connection_id", None)
    return cleaned


def _status_error(provider: str, response: httpx.Response) -> ToolExecuteResponse:
    """把 connector-service 的状态码映射成工具错误码(设计 §5.7)。

    工具级失败(如无可用连接)在本层仍表现为 ok=false 的工具信封,由调用方
    决定是否重试;401 说明内部密钥不一致,属于部署错误,不可重试。
    """

    body = response.text[:ERROR_BODY_LIMIT]
    detail = f"connector-service 返回 {response.status_code}:{body}"
    if response.status_code == 404:
        return _error(
            "tool.connector_unavailable",
            f"连接器 {provider} 没有可用连接",
            hint=detail,
        )
    if response.status_code == 401:
        return _error(
            "tool.connector_unavailable",
            "connector-service 拒绝了内部调用",
            hint=detail,
        )
    if 400 <= response.status_code < 500:
        return _error(
            "tool.connector_unavailable",
            f"连接器 {provider} 拒绝执行",
            hint=detail,
        )
    return _error(
        "tool.connector_unavailable",
        f"connector-service 返回 {response.status_code}",
        hint=detail,
        retryable=True,
    )


class ConnectorGateway:
    """连接器目录与执行的统一入口;一个实例可被并发请求复用。

    目录按账号做 TTL 缓存(默认 30 秒):连接状态变更最多延迟一个 TTL 生效;
    拉取失败不缓存,下次请求立即重试。
    """

    def __init__(
        self,
        *,
        base_url: str,
        internal_secret: str,
        ttl_seconds: float = 30.0,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._secret = internal_secret
        self._ttl = max(0.0, float(ttl_seconds))
        self._cache: dict[str, tuple[float, tuple[ToolDescriptor, ...]]] = {}
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"accept": "application/json"},
        )

    @property
    def enabled(self) -> bool:
        """未配置内部密钥时连接器整体关闭(目录不含连接器工具)。"""

        return bool(self._secret)

    async def aclose(self) -> None:
        """关闭底层连接池(装配方在 shutdown 阶段调用)。"""

        await self._client.aclose()

    # ---- 目录 -----------------------------------------------------------

    async def tools(self, account_id: str) -> tuple[ToolDescriptor, ...]:
        """按账号返回连接器工具;未启用或拉取失败一律返回空(降级)。"""

        if not self.enabled:
            return EMPTY_TOOLS
        cached = self._cache.get(account_id)
        if cached is not None and time.monotonic() - cached[0] < self._ttl:
            return cached[1]
        async with self._lock:
            cached = self._cache.get(account_id)
            if cached is not None and time.monotonic() - cached[0] < self._ttl:
                return cached[1]
            tools = await self._fetch_catalog(account_id)
            if tools is None:
                return EMPTY_TOOLS
            self._cache[account_id] = (time.monotonic(), tools)
            return tools

    async def find(self, account_id: str, name: str) -> ToolDescriptor | None:
        """按正式名在连接器目录里查 descriptor(未知返回 None)。"""

        for tool in await self.tools(account_id):
            if tool.name == name:
                return tool
        return None

    async def _fetch_catalog(
        self, account_id: str
    ) -> tuple[ToolDescriptor, ...] | None:
        """拉一次内部目录;任何失败返回 None(调用方降级为空)。"""

        try:
            response = await self._client.get(
                CATALOG_PATH, headers=self._headers(account_id)
            )
        except httpx.HTTPError as exc:
            log.warning(
                "connector_catalog_unreachable",
                account_id=account_id,
                error=type(exc).__name__,
            )
            return None
        if response.status_code != 200:
            log.warning(
                "connector_catalog_rejected",
                account_id=account_id,
                status_code=response.status_code,
            )
            return None
        try:
            payload: Any = response.json()
        except ValueError:
            log.warning("connector_catalog_bad_json", account_id=account_id)
            return None
        raw_tools = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(raw_tools, list):
            log.warning("connector_catalog_missing_tools", account_id=account_id)
            return None
        tools: list[ToolDescriptor] = []
        for item in raw_tools:
            try:
                descriptor = ToolDescriptor.from_payload(item)
            except (TypeError, ValueError) as exc:
                log.warning(
                    "connector_catalog_bad_descriptor",
                    account_id=account_id,
                    error=str(exc),
                )
                continue
            if descriptor.executor is not ExecutorKind.CONNECTOR:
                log.warning(
                    "connector_catalog_wrong_executor",
                    account_id=account_id,
                    tool=descriptor.name,
                    executor=str(descriptor.executor),
                )
                continue
            tools.append(descriptor)
        return tuple(tools)

    # ---- 执行 -----------------------------------------------------------

    async def execute(
        self,
        account_id: str,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        *,
        run_id: str | None = None,
    ) -> ConnectorExecution:
        """把一次工具调用转发给 connector-service,透传工具信封并带回连接 id。

        成功信封里的顶层 connection_id 是审计字段:先取出再从 body 拷贝中剥离,
        确保它不会混进模型可见的 result(C2.4)。
        """

        provider = _provider_of(descriptor)
        if provider is None:
            return ConnectorExecution(
                _error(
                    "tool.executor_unavailable",
                    f"连接器工具 {descriptor.name} 缺少 provider 声明",
                    hint="descriptor.connector.provider 必须是非空字符串",
                )
            )
        payload: dict[str, Any] = {
            "tool": descriptor.name,
            "arguments": arguments,
        }
        if run_id:
            payload["run_id"] = run_id
        url = EXECUTE_PATH.format(provider=quote(provider, safe=""))
        try:
            response = await self._client.post(
                url, json=payload, headers=self._headers(account_id)
            )
        except httpx.TimeoutException:
            return ConnectorExecution(
                _error(
                    "tool.connector_timeout",
                    f"连接器 {provider} 执行超时",
                    hint="请稍后重试",
                    retryable=True,
                )
            )
        except httpx.HTTPError as exc:
            return ConnectorExecution(
                _error(
                    "tool.connector_unavailable",
                    f"连接器 {provider} 不可达",
                    hint=str(exc),
                    retryable=True,
                )
            )
        if response.status_code >= 400:
            return ConnectorExecution(_status_error(provider, response))
        try:
            body: Any = response.json()
        except ValueError:
            return ConnectorExecution(
                _error(
                    "tool.connector_unavailable",
                    f"连接器 {provider} 返回非 JSON 响应",
                    retryable=True,
                )
            )
        if not isinstance(body, dict):
            return ConnectorExecution(
                _error(
                    "tool.connector_unavailable",
                    f"连接器 {provider} 返回非工具信封",
                    retryable=True,
                )
            )
        connection_id = _connection_id_of(body)
        try:
            return ConnectorExecution(
                ToolExecuteResponse.from_payload(_strip_connection_id(body)),
                connection_id=connection_id,
            )
        except ValueError as exc:
            return ConnectorExecution(
                _error(
                    "tool.connector_unavailable",
                    f"连接器 {provider} 响应信封非法",
                    hint=str(exc),
                    retryable=True,
                )
            )

    def _headers(self, account_id: str) -> dict[str, str]:
        return {
            SECRET_HEADER: self._secret,
            ACCOUNT_HEADER: account_id,
            "content-type": "application/json",
        }
