"""公开 MCP Server 的 API Key 校验与 JSON-RPC 处理(M3 本地版)。

传输:Streamable HTTP;本实现按请求返回 JSON(或单帧 SSE),不做长连接。
鉴权:Authorization: Bearer bm_live_... → account-service 内部校验。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from common.security import SECRET_HEADER
from common.errors import (
    AppError,
    NotFoundError,
    PermissionDeniedError,
    UnauthorizedError,
    UpstreamError,
)
from database.session import session_scope

from agent_orchestrator.models.runs import RunArtifact
from agent_orchestrator.repositories import ArtifactRepository, RunRepository
from agent_orchestrator.services import artifacts as artifacts_service
from agent_orchestrator.services.tools import ToolRegistry, ToolRisk

PROTOCOL_VERSION = "2025-03-26"


@dataclass(frozen=True)
class MCPPrincipal:
    tenant_id: str
    api_key_id: str
    scopes: frozenset[str]


class OAuthTokenIntrospector:
    """OAuth 2.1 access token 内省;非 active 一律视为无效。"""

    def __init__(
        self,
        *,
        base_url: str,
        internal_secret: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._secret = internal_secret

    async def introspect(self, token: str) -> MCPPrincipal | None:
        if not self._secret:
            return None
        try:
            response = await self._client.post(
                "/v1/oauth/introspect",
                json={"token": token},
                headers={SECRET_HEADER: self._secret},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "mcp.auth_unavailable", f"OAuth introspect 服务不可达:{exc}"
            ) from exc
        if response.status_code != 200:
            return None
        body = response.json()
        if not body.get("active"):
            return None
        return MCPPrincipal(
            tenant_id=str(body["tenant_id"]),
            api_key_id=str(body.get("client_id") or "oauth"),
            scopes=frozenset(str(body.get("scope") or "").split()),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class ApiKeyVerifier:
    """account-service 内部校验客户端;失败一律 401,不泄漏细节。"""

    def __init__(
        self,
        *,
        base_url: str,
        internal_secret: str,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._secret = internal_secret

    async def verify(self, key: str) -> MCPPrincipal:
        if not self._secret:
            raise UnauthorizedError("mcp.auth_unavailable", "公开 MCP 鉴权未配置")
        try:
            response = await self._client.post(
                "/v1/api-keys/verify",
                json={"key": key},
                headers={SECRET_HEADER: self._secret},
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(
                "mcp.auth_unavailable", f"API Key 校验服务不可达:{exc}"
            ) from exc
        if response.status_code != 200:
            raise UnauthorizedError("mcp.unauthorized", "API Key 无效或已吊销")
        body = response.json()
        return MCPPrincipal(
            tenant_id=str(body["tenant_id"]),
            api_key_id=str(body["api_key_id"]),
            scopes=frozenset(body.get("scopes") or []),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _require_scope(principal: MCPPrincipal, scope: str) -> None:
    if scope not in principal.scopes:
        raise PermissionDeniedError("mcp.forbidden", f"缺少 scope:{scope}")


def _read_only_tools(toolkit: ToolRegistry | None) -> list[dict[str, Any]]:
    if toolkit is None:
        return []
    out: list[dict[str, Any]] = []
    for schema in toolkit.schemas():
        name = schema["function"]["name"]
        if name.startswith("code_"):
            continue  # 公开 MCP 不暴露服务端本地文件工具
        if toolkit.risk(name) is not ToolRisk.READ:
            continue  # 公开面只暴露只读工具;受控写/危险操作走控制台(有审批/沙箱)
        out.append(
            {
                "name": f"beauty.{name}",
                "description": schema["function"].get("description") or name,
                "inputSchema": schema["function"].get("parameters")
                or {"type": "object"},
            }
        )
    return out


async def _run_tools(factory, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
    async with session_scope(factory) as session:
        runs = await RunRepository(session).list_recent(tenant_id, limit)
    return [
        {
            "id": run.id,
            "title": run.title,
            "status": run.status,
            "skill_id": run.skill_id,
            "created_at": run.created_at.isoformat(),
        }
        for run in runs
    ]


async def _artifact_read(
    factory, tenant_id: str, artifact_id: str, store: Any = None
) -> dict[str, Any]:
    """按 id 取交付物并装载文本;二进制件不返回正文(MCP 只承载文本)。"""
    async with session_scope(factory) as session:
        artifact = await ArtifactRepository(session).require_for_tenant(
            tenant_id, artifact_id
        )
        text = await artifacts_service.read_artifact_text(artifact, store)
        mime_type = artifact.content_type or "text/markdown"
        uri = f"beauty://artifacts/{artifact.id}"
    if text is None:
        raise AppError(
            "mcp.artifact_not_inline",
            "该交付物是二进制文件,不支持 MCP 文本读取;请通过下载端点获取",
        )
    return {"uri": uri, "mimeType": mime_type, "text": text}


async def _list_resources(factory, tenant_id: str, limit: int = 20):
    async with session_scope(factory) as session:
        artifacts = await ArtifactRepository(session).list_scoped(
            tenant_id, limit=limit, order_by=[RunArtifact.created_at.desc()]
        )
    return [
        {
            "uri": f"beauty://artifacts/{artifact.id}",
            "name": artifact.title,
            "title": artifact.title,
            "mimeType": artifact.content_type or "text/markdown",
        }
        for artifact in artifacts
    ]


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


async def call_tool(
    request,
    principal: MCPPrincipal,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    factory = request.app.state.session_factory
    if name == "beauty.whoami":
        return _tool_result(
            json.dumps(
                {
                    "tenant_id": principal.tenant_id,
                    "api_key_id": principal.api_key_id,
                    "scopes": sorted(principal.scopes),
                },
                ensure_ascii=False,
            )
        )
    if name == "beauty.list_runs":
        runs = await _run_tools(factory, principal.tenant_id)
        return _tool_result(json.dumps({"runs": runs}, ensure_ascii=False))
    if name == "beauty.get_artifact":
        artifact_id = str(arguments.get("artifact_id") or "")
        if not artifact_id:
            return _tool_result("缺少 artifact_id", is_error=True)
        try:
            content = await _artifact_read(
                factory,
                principal.tenant_id,
                artifact_id,
                getattr(request.app.state, "artifact_store", None),
            )
        except AppError as exc:
            return _tool_result(exc.message, is_error=True)
        return _tool_result(content["text"])
    # 只读工具走与内部 run 相同的 ToolRegistry
    toolkit_factory = getattr(request.app.state, "toolkit_factory", None)
    toolkit = toolkit_factory(principal.tenant_id) if toolkit_factory else None
    if toolkit is None:
        return _tool_result("工具未配置", is_error=True)
    tool_name = name.removeprefix("beauty.")
    if toolkit.requires_confirmation(tool_name):
        return _tool_result("公开 MCP 不执行写操作", is_error=True)
    output = await toolkit.execute(tool_name, json.dumps(arguments, ensure_ascii=False))
    return _tool_result(output, is_error=output.startswith('{"error"'))


def _builtin_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "beauty.whoami",
            "description": "查看当前 API Key 的租户与 scopes",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "beauty.list_runs",
            "description": "列出当前租户最近的 run(只读)",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "beauty.get_artifact",
            "description": "读取一个 run 交付物的 Markdown 正文",
            "inputSchema": {
                "type": "object",
                "properties": {"artifact_id": {"type": "string"}},
                "required": ["artifact_id"],
            },
        },
    ]


async def handle_jsonrpc(request, principal: MCPPrincipal, payload: dict) -> dict:
    method = payload.get("method")
    params = payload.get("params") or {}
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "beauty-moon", "version": "0.1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        _require_scope(principal, "mcp:tools:read")
        toolkit_factory = getattr(request.app.state, "toolkit_factory", None)
        toolkit = toolkit_factory(principal.tenant_id) if toolkit_factory else None
        return {"tools": [*_builtin_tools(), *_read_only_tools(toolkit)]}
    if method == "tools/call":
        _require_scope(principal, "mcp:tools:call")
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _tool_result("arguments 必须是对象", is_error=True)
        return await call_tool(request, principal, name, arguments)
    if method == "resources/list":
        _require_scope(principal, "artifacts:read")
        return {
            "resources": await _list_resources(
                request.app.state.session_factory, principal.tenant_id
            )
        }
    if method == "resources/read":
        _require_scope(principal, "artifacts:read")
        uri = str(params.get("uri") or "")
        artifact_id = uri.removeprefix("beauty://artifacts/")
        if not artifact_id or artifact_id == uri:
            raise NotFoundError("mcp.resource_not_found", "资源 URI 无效")
        try:
            return {
                "contents": [
                    await _artifact_read(
                        request.app.state.session_factory,
                        principal.tenant_id,
                        artifact_id,
                        getattr(request.app.state, "artifact_store", None),
                    )
                ]
            }
        except AppError as exc:
            raise NotFoundError("mcp.resource_not_found", exc.message) from exc
    raise NotFoundError("mcp.method_not_found", f"未知 MCP 方法:{method}")
