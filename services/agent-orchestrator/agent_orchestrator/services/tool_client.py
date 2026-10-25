"""tool-service 的 HTTP 客户端与远端工具箱适配器(设计 §12.3)。

orchestrator 不再在本进程里注册内置工具:工具目录与执行都委托给
tool-service(:8008),本模块负责三件事:

- ToolClient:GET /v1/tools 拉目录、POST /v1/tools/{name}:execute 执行;
- ToolCatalog:进程级目录缓存(首次使用拉一次,失败下次重试;refresh 显式刷新);
- RemoteToolkit / HybridToolkit:实现 graph.ports.ToolkitPort,保持旧
  ToolRegistry 的 OpenAI schema 形状与「执行永不抛、错误 JSON 回喂」契约,
  并把三档风险映射回旧枚举(read/write/danger)供审批闸使用;execute() 的
  approved/tool_call_id 原样透传给 tool-service——审批凭据由调用方给出,
  不再按风险自动补 approved(P1 受控写)。

服务不可用时的口径:目录拉不到 -> ensure_ready 抛 UpstreamError(装配方
按必需依赖处理,不静默降级到本机直读);执行期不可达 -> 回喂错误信封
(retryable=true),让模型决策,不炸整条 run。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx
from common.errors import UpstreamError
from common.tools import (
    ToolDescriptor,
    ToolError,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolKind,
    ToolRisk,
    to_legacy_risk,
)

from agent_orchestrator.services.tool_context import current_tool_context
from agent_orchestrator.services.tools import ToolRegistry, ToolSpec

TokenProvider = Callable[[str], Awaitable[str]]

#: tool-service 不可达(网络/超时/DNS)。
UNREACHABLE_CODE = "tool_service.unreachable"
#: tool-service 响应不符合契约(非 JSON/信封非法/HTTP 5xx)。
UPSTREAM_ERROR_CODE = "tool_service.upstream_error"

#: 工具服务调用超时 = 服务端工具超时 + 这段余量(传输/排队)。
DEFAULT_TIMEOUT_GRACE_SECONDS = 1.0


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
        if self._token_provider is not None:
            try:
                token = await self._token_provider(tenant_id)
            except Exception as exc:  # noqa: BLE001 - 换发失败按上游故障回喂
                raise UpstreamError(
                    UNREACHABLE_CODE, f"获取服务间 token 失败: {type(exc).__name__}"
                ) from exc
            headers["authorization"] = f"Bearer {token}"
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


class ToolCatalog:
    """进程级工具目录缓存(多请求共享;失败不缓存)。"""

    def __init__(self) -> None:
        self._descriptors: tuple[ToolDescriptor, ...] = ()
        self._version = ""
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    @property
    def version(self) -> str:
        return self._version

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def ensure(self, client: ToolClient) -> None:
        """确保目录已加载:并发调用只发一次请求,失败后下次重试。"""

        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            tools, version = await client.list_tools()
            self._descriptors = tools
            self._version = version
            self._loaded = True

    async def refresh(self, client: ToolClient) -> None:
        """强制刷新目录(控制台/运维显式触发;P0 不自动轮询)。"""

        async with self._lock:
            tools, version = await client.list_tools()
            self._descriptors = tools
            self._version = version
            self._loaded = True


class RemoteToolkit:
    """把 tool-service 目录包装成 graph 侧 ToolkitPort(按租户实例化)。

    - schemas():只暴露可执行的普通工具(kind=function);交互工具由执行图的
      提问闸处理,不进模型工具清单;
    - risk():返回旧枚举 read/write/danger,审批闸与 golden-run 断言无需改;
    - execute():参数 JSON 解析失败/未知工具/服务不可达一律回错误信封。
    """

    def __init__(
        self,
        tenant_id: str,
        *,
        client: ToolClient,
        catalog: ToolCatalog,
        timeout_grace_seconds: float = DEFAULT_TIMEOUT_GRACE_SECONDS,
        allowed: frozenset[str] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._client = client
        self._catalog = catalog
        self._timeout_grace = timeout_grace_seconds
        self._allowed = allowed

    async def ensure_ready(self) -> None:
        """惰性加载目录;图节点/对话循环在拿到 toolkit 后显式等待一次。"""

        await self._catalog.ensure(self._client)

    def __len__(self) -> int:
        return len(self._function_tools())

    @property
    def names(self) -> list[str]:
        return [tool.name for tool in self._function_tools()]

    def contains(self, name: str) -> bool:
        canonical = self._canonical(name)
        return canonical is not None and canonical in {
            t.name for t in self._function_tools()
        }

    @property
    def has_dangerous(self) -> bool:
        return any(tool.risk is ToolRisk.DESTRUCTIVE for tool in self._function_tools())

    def risk(self, name: str) -> str:
        """旧枚举风险值(read/write/danger);未知工具按最严处理。"""

        descriptor = self._descriptor(name)
        if descriptor is None:
            return "danger"
        return to_legacy_risk(descriptor.risk)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._function_tools()
        ]

    def extended(self, specs: list[ToolSpec]) -> HybridToolkit:
        """合并 orchestrator 侧工具(MCP / skill_run),内置工具仍在远端。"""

        return HybridToolkit(self, ToolRegistry(list(specs)))

    def restricted(self, allowed: list[str]) -> RemoteToolkit:
        """按 Skill/对话白名单裁剪;白名单里的旧别名映射回正式名。"""

        canonical: set[str] = set()
        for name in allowed:
            found = self._canonical(name)
            if found is not None:
                canonical.add(found)
        if self._allowed is not None:
            canonical &= self._allowed
        return RemoteToolkit(
            self._tenant_id,
            client=self._client,
            catalog=self._catalog,
            timeout_grace_seconds=self._timeout_grace,
            allowed=frozenset(canonical),
        )

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        """执行工具;契约同旧 ToolRegistry:永不向图抛异常。

        tool_call_id 是调用方给的幂等键(缺省生成 orch-*),approved 是审批
        流程放行的凭据(策略 auto / 人工确认之后)——这里只做透传,不再按
        风险自动补:是否放行由调用方(图节点/对话循环)决定。
        """

        call_id = tool_call_id or f"orch-{uuid.uuid4().hex}"
        descriptor = self._descriptor(name)
        if descriptor is None:
            return _error_envelope(
                "tool.not_found",
                f"未知工具: {name}",
                hint="只允许调用 schemas() 中列出的工具",
                tool_call_id=call_id,
            )
        if descriptor.kind is not ToolKind.FUNCTION:
            return _error_envelope(
                "tool.not_supported",
                f"工具 {descriptor.name} 需要人工交互,不能在执行图里直接调用",
                tool_call_id=call_id,
            )
        try:
            payload = json.loads(arguments) if arguments else {}
        except ValueError as exc:
            return _error_envelope(
                "tool.invalid_args",
                f"工具参数不是合法 JSON: {exc}",
                tool_call_id=call_id,
            )
        if not isinstance(payload, dict):
            return _error_envelope(
                "tool.invalid_args",
                "工具参数必须是 JSON 对象",
                tool_call_id=call_id,
            )
        context = current_tool_context()
        request = ToolExecuteRequest(
            tool_call_id=call_id,
            arguments=payload,
            run_id=context.run_id if context is not None else None,
            conversation_id=context.conversation_id if context is not None else None,
            # 审批凭据完全由调用方决定(执行图:策略 auto 或人工确认后传
            # True;对话路径恒 False,写工具由服务端风险下限兜底)。
            approved=approved,
        )
        timeout = descriptor.timeout_seconds + self._timeout_grace
        try:
            response = await asyncio.wait_for(
                self._client.execute(self._tenant_id, descriptor.name, request),
                timeout=timeout,
            )
        except TimeoutError:
            return _error_envelope(
                "tool.timeout",
                f"工具 {descriptor.name} 执行超时({timeout:g}s)",
                retryable=True,
                tool_call_id=call_id,
            )
        except UpstreamError as exc:
            return _error_envelope(
                exc.code,
                exc.message,
                hint="tool-service 暂不可用,可稍后重试",
                retryable=True,
                tool_call_id=call_id,
            )
        if not response.tool_call_id:
            # 服务端没回显幂等键时补上本次请求的,保证审计/回放能对上。
            response = replace(response, tool_call_id=call_id)
        return json.dumps(response.envelope(), ensure_ascii=False)

    def _function_tools(self) -> tuple[ToolDescriptor, ...]:
        return tuple(
            tool for tool in self._visible_tools() if tool.kind is ToolKind.FUNCTION
        )

    def _visible_tools(self) -> tuple[ToolDescriptor, ...]:
        tools = self._catalog.descriptors
        if self._allowed is None:
            return tools
        return tuple(tool for tool in tools if tool.name in self._allowed)

    def _descriptor(self, name: str) -> ToolDescriptor | None:
        canonical = self._canonical(name)
        if canonical is None:
            return None
        for tool in self._visible_tools():
            if tool.name == canonical:
                return tool
        return None

    def _canonical(self, name: str) -> str | None:
        for tool in self._visible_tools():
            if tool.name == name:
                return tool.name
        for tool in self._visible_tools():
            if name in tool.aliases:
                return tool.name
        return None


class HybridToolkit:
    """远端内置工具 + orchestrator 侧工具(MCP / skill_run)的合并视图。"""

    def __init__(self, base: RemoteToolkit, extra: ToolRegistry) -> None:
        self._base = base
        self._extra = extra

    def __len__(self) -> int:
        return len(self._base) + len(self._extra)

    @property
    def names(self) -> list[str]:
        return [*self._base.names, *self._extra.names]

    @property
    def has_dangerous(self) -> bool:
        return self._base.has_dangerous or self._extra.has_dangerous

    def risk(self, name: str) -> str:
        return (
            self._base.risk(name)
            if _contains(self._base, name)
            else self._extra.risk(name)
        )

    def schemas(self) -> list[dict[str, Any]]:
        return [*self._base.schemas(), *self._extra.schemas()]

    def extended(self, specs: list[ToolSpec]) -> HybridToolkit:
        return HybridToolkit(self._base, self._extra.extended(specs))

    def restricted(self, allowed: list[str]) -> HybridToolkit:
        return HybridToolkit(
            self._base.restricted(allowed), self._extra.restricted(allowed)
        )

    async def execute(
        self,
        name: str,
        arguments: str,
        *,
        tool_call_id: str | None = None,
        approved: bool = False,
    ) -> str:
        if _contains(self._base, name):
            return await self._base.execute(
                name, arguments, tool_call_id=tool_call_id, approved=approved
            )
        if _contains(self._extra, name):
            return await self._extra.execute(
                name, arguments, tool_call_id=tool_call_id, approved=approved
            )
        return _error_envelope(
            "tool.not_found",
            f"未知工具: {name}",
            tool_call_id=tool_call_id or f"orch-{uuid.uuid4().hex}",
        )


def _contains(toolkit: Any, name: str) -> bool:
    """判断工具箱是否认识这个名字(远端与旧注册表接口口径不同)。"""

    hook = getattr(toolkit, "contains", None)
    if callable(hook):
        return bool(hook(name))
    names = getattr(toolkit, "names", None)
    return bool(names is not None and name in names)


def _decode_json(response: httpx.Response, *, expected: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(
            UPSTREAM_ERROR_CODE,
            f"tool-service {expected}响应不是 JSON(HTTP {response.status_code})",
        ) from exc
    if response.status_code >= 400:
        error = _error_from_http(payload, status=response.status_code)
        raise UpstreamError(error.code, error.message)
    if not isinstance(payload, dict):
        raise UpstreamError(
            UPSTREAM_ERROR_CODE, f"tool-service {expected}响应必须是 JSON 对象"
        )
    return payload


def _error_from_http(payload: Any, *, status: int) -> ToolError:
    """把 tool-service 的 API 错误体映射成工具错误信封。"""

    if isinstance(payload, dict) and isinstance(payload.get("code"), str):
        return ToolError(
            code=str(payload["code"]),
            message=str(
                payload.get("message") or f"tool-service 调用失败(HTTP {status})"
            ),
            retryable=status >= 500,
        )
    return ToolError(
        code=UPSTREAM_ERROR_CODE,
        message=f"tool-service 调用失败(HTTP {status})",
        retryable=status >= 500,
    )


def _error_envelope(
    code: str,
    message: str,
    *,
    hint: str = "",
    retryable: bool = False,
    tool_call_id: str = "",
) -> str:
    response = ToolExecuteResponse(
        ok=False,
        error=ToolError(code=code, message=message, hint=hint, retryable=retryable),
        tool_call_id=tool_call_id,
    )
    return json.dumps(response.envelope(), ensure_ascii=False)
