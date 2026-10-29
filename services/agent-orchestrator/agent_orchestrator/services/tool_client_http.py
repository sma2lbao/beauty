"""tool-service 的 HTTP 客户端(自 tool_client.py 拆出)。

GET /v1/tools 不鉴权(目录是公开部署信息);执行与审计端点带租户 JWT,经装配方
注入的 token_provider 现取,不在进程内长期保存明文 token。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from common.errors import UpstreamError
from common.tools import (
    ToolDescriptor,
    ToolError,
    ToolExecuteRequest,
    ToolExecuteResponse,
)

from agent_orchestrator.services.tool_client_support import (
    UNREACHABLE_CODE,
    UPSTREAM_ERROR_CODE,
    TokenProvider,
    _decode_json,
    _error_from_http,
)


class ToolClient:
    """tool-service 的 HTTP 客户端(仅服务间内部调用)。

    GET /v1/tools 不鉴权(目录是公开部署信息);执行端点带租户 JWT,
    经装配方注入的 token_provider 现取,不在进程内长期保存明文 token。
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 35.0,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"accept": "application/json"},
        )

    async def aclose(self) -> None:
        """关闭底层连接池(装配方在 shutdown 阶段调用)。"""

        await self._client.aclose()

    async def list_tools(self) -> tuple[tuple[ToolDescriptor, ...], str]:
        """拉取工具目录;响应非法一律按上游契约错误处理。"""

        try:
            response = await self._client.get("/v1/tools")
        except httpx.HTTPError as exc:
            raise UpstreamError(
                UNREACHABLE_CODE, f"tool-service 不可达: {type(exc).__name__}"
            ) from exc
        payload = _decode_json(response, expected="工具目录")
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list):
            raise UpstreamError(UPSTREAM_ERROR_CODE, "tool-service 目录响应缺少 tools")
        try:
            tools = tuple(ToolDescriptor.from_payload(item) for item in raw_tools)
        except (TypeError, ValueError) as exc:
            raise UpstreamError(
                UPSTREAM_ERROR_CODE, f"tool-service 目录响应非法: {exc}"
            ) from exc
        version = payload.get("catalog_version")
        return tools, str(version or "")

    async def execute(
        self, tenant_id: str, tool_name: str, request: ToolExecuteRequest
    ) -> ToolExecuteResponse:
        """执行一次工具调用。

        ToolExecuteRequest 里的 tool_call_id/approved 原样进请求体:前者是
        服务端幂等键,后者是审批放行凭据(P1 受控写)。HTTP 200 是正常路径
        (失败也在信封里);其余状态码映射成错误信封,只有传输层故障才抛
        UpstreamError(由 RemoteToolkit 统一回喂)。
        """

        headers: dict[str, str] = {"content-type": "application/json"}
        headers.update(await self._auth_headers(tenant_id))
        url = f"/v1/tools/{quote(tool_name, safe='')}:execute"
        try:
            response = await self._client.post(
                url, json=request.to_payload(), headers=headers
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                UNREACHABLE_CODE, f"tool-service 不可达: {type(exc).__name__}"
            ) from exc
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            return ToolExecuteResponse(
                ok=False,
                error=_error_from_http(payload, status=response.status_code),
                tool_call_id=request.tool_call_id,
            )
        if not isinstance(payload, dict):
            return ToolExecuteResponse(
                ok=False,
                error=ToolError(
                    code=UPSTREAM_ERROR_CODE,
                    message="tool-service 返回非 JSON 对象",
                    retryable=True,
                ),
                tool_call_id=request.tool_call_id,
            )
        try:
            return ToolExecuteResponse.from_payload(payload)
        except ValueError as exc:
            return ToolExecuteResponse(
                ok=False,
                error=ToolError(
                    code=UPSTREAM_ERROR_CODE,
                    message=f"tool-service 响应信封非法: {exc}",
                    retryable=True,
                ),
                tool_call_id=request.tool_call_id,
            )

    async def list_tool_calls(
        self,
        tenant_id: str,
        *,
        conversation_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """按会话跨 run 查询工具调用审计(含服务端补丁)。

        前端原本只能从工具调用参数本地重建补丁,受事件参数截断
        (MAX_EVENT_ARGUMENTS_CHARS=2000)影响;这里改读 tool-service 落库的
        unified diff,补丁长度不再受事件体限制。返回原始 JSON,由 API 层用
        schema 逐条校验。
        """

        params: dict[str, Any] = {
            "conversation_id": conversation_id,
            "include_patch": "true",
            "limit": limit,
            "offset": offset,
        }
        try:
            response = await self._client.get(
                "/v1/tool-calls",
                params=params,
                headers=await self._auth_headers(tenant_id),
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                UNREACHABLE_CODE, f"tool-service 不可达: {type(exc).__name__}"
            ) from exc
        payload = _decode_json(response, expected="工具调用审计")
        if not isinstance(payload.get("items"), list):
            raise UpstreamError(UPSTREAM_ERROR_CODE, "tool-service 审计响应缺少 items")
        if not isinstance(payload.get("total"), int):
            raise UpstreamError(UPSTREAM_ERROR_CODE, "tool-service 审计响应 total 非法")
        return payload

    async def _auth_headers(self, tenant_id: str) -> dict[str, str]:
        """现取服务间 token 并拼 Bearer 头;换发失败按上游故障处理。

        不缓存明文 token:租户来自签名 claim,每次调用现取。
        """

        if self._token_provider is None:
            return {}
        try:
            token = await self._token_provider(tenant_id)
        except Exception as exc:  # noqa: BLE001 - 换发失败按上游故障回喂
            raise UpstreamError(
                UNREACHABLE_CODE, f"获取服务间 token 失败: {type(exc).__name__}"
            ) from exc
        return {"authorization": f"Bearer {token}"}
