"""tool-service 客户端与远端工具箱(设计 §12.3)。

覆盖三件事:HTTP 客户端(目录/执行信封/错误映射)、目录缓存(并发只拉一次、
失败不缓存)、RemoteToolkit/HybridToolkit 的 ToolkitPort 契约(风险映射、别名、
超时、上下文透传、与 MCP 工具合并)。
本文件承载原 ``test_tool_client.py`` 拆分后的会话级工具调用审计查询部分。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from common.errors import UpstreamError
from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk

from agent_orchestrator.services.tool_client import (
    UNREACHABLE_CODE,
    UPSTREAM_ERROR_CODE,
    RemoteToolkit,
    ToolCatalog,
    ToolClient,
)

BASE_URL = "http://tool.local"
Handler = Callable[[httpx.Request], Any]


def _descriptor(
    name: str = "read",
    *,
    risk: ToolRisk = ToolRisk.READ,
    kind: ToolKind = ToolKind.FUNCTION,
    timeout_seconds: float = 20.0,
    aliases: tuple[str, ...] = ("legacy_local_read",),
) -> ToolDescriptor:
    """构造一份工具描述(默认只读文件工具,带一个 legacy 别名)。"""

    return ToolDescriptor(
        name=name,
        description=f"{name} 工具",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        risk=risk,
        kind=kind,
        executor=ExecutorKind.SANDBOX,
        timeout_seconds=timeout_seconds,
        aliases=aliases,
    )


def _catalog(*descriptors: ToolDescriptor, version: str = "1") -> dict[str, Any]:
    return {
        "tools": [item.to_payload() for item in descriptors],
        "catalog_version": version,
    }


def _ok_envelope(text: str = "hello") -> dict[str, Any]:
    return {
        "ok": True,
        "path": "a.txt",
        "content": text,
        "truncated": False,
        "duration_ms": 5,
    }


def _client(handler: Handler, *, token_provider: Any = None) -> ToolClient:
    return ToolClient(
        BASE_URL,
        transport=httpx.MockTransport(handler),
        token_provider=token_provider,
    )


def _server(
    catalog: dict[str, Any],
    exec_handler: Handler | None = None,
    seen: list[httpx.Request] | None = None,
) -> Handler:
    """路由替身:GET 返回目录,POST 交给 exec_handler(默认成功信封)。"""

    def handler(request: httpx.Request) -> Any:
        if seen is not None:
            seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=catalog)
        if exec_handler is not None:
            return exec_handler(request)
        return httpx.Response(200, json=_ok_envelope())

    return handler


async def _ready(
    handler: Handler, *descriptors: ToolDescriptor, **kwargs: Any
) -> tuple[RemoteToolkit, ToolCatalog, ToolClient]:
    """构造已加载目录的 RemoteToolkit。"""

    client = _client(handler)
    catalog = ToolCatalog()
    toolkit = RemoteToolkit("t-1", client=client, catalog=catalog, **kwargs)
    await toolkit.ensure_ready()
    return toolkit, catalog, client


async def _token(tenant_id: str) -> str:
    return f"tok-{tenant_id}"


# ------------------------------------------------------ 会话级工具调用审计


def _audit_item(**overrides: Any) -> dict[str, Any]:
    """一条 tool-service 审计行(字段与 ToolCallRead 对齐)。"""

    item: dict[str, Any] = {
        "id": "tc-1",
        "tool_call_id": "call-1",
        "run_id": "run-1",
        "conversation_id": "c-1",
        "tool_name": "write",
        "tool_version": "1",
        "risk": "write",
        "decision": "auto",
        "ok": True,
        "error_code": None,
        "duration_ms": 12,
        "result_chars": 40,
        "bytes_written": 12,
        "truncated": False,
        "sha256_before": None,
        "sha256_after": "a" * 64,
        "paths": ["a.txt"],
        "patch": "--- a/a.txt\n+++ b/a.txt\n",
        "lines_added": 1,
        "lines_removed": 1,
        "patch_truncated": False,
        "sandbox_id": "sbx-1",
        "created_at": "2026-01-01T00:00:00Z",
    }
    item.update(overrides)
    return item


def _audit_page(*items: dict[str, Any], total: int | None = None) -> dict[str, Any]:
    return {
        "items": list(items),
        "total": len(items) if total is None else total,
        "limit": 50,
        "offset": 0,
    }


async def test_list_tool_calls_sends_filters_and_bearer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_audit_page(_audit_item()))

    client = _client(handler, token_provider=_token)
    payload = await client.list_tool_calls(
        "t-9", conversation_id="c-1", limit=10, offset=5
    )

    assert payload["total"] == 1
    assert payload["items"][0]["patch"].startswith("--- a/")
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/v1/tool-calls"
    assert request.url.params["conversation_id"] == "c-1"
    assert request.url.params["include_patch"] == "true"
    assert request.url.params["limit"] == "10"
    assert request.url.params["offset"] == "5"
    assert request.headers["authorization"] == "Bearer tok-t-9"
    await client.aclose()


async def test_list_tool_calls_without_token_provider_sends_no_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_audit_page())

    client = _client(handler)
    await client.list_tool_calls("t-1", conversation_id="c-1")
    assert "authorization" not in seen[0].headers
    assert seen[0].url.params["include_patch"] == "true"
    await client.aclose()


async def test_list_tool_calls_rejects_missing_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total": 0, "limit": 50, "offset": 0})

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tool_calls("t-1", conversation_id="c-1")
    assert exc_info.value.code == UPSTREAM_ERROR_CODE
    assert "items" in exc_info.value.message
    await client.aclose()


async def test_list_tool_calls_rejects_missing_total() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tool_calls("t-1", conversation_id="c-1")
    assert exc_info.value.code == UPSTREAM_ERROR_CODE
    assert "total" in exc_info.value.message
    await client.aclose()


async def test_list_tool_calls_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tool_calls("t-1", conversation_id="c-1")
    assert exc_info.value.code == UNREACHABLE_CODE
    await client.aclose()


async def test_list_tool_calls_http_error_uses_service_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"code": "tool_service.down", "message": "维护中"}
        )

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tool_calls("t-1", conversation_id="c-1")
    assert exc_info.value.code == "tool_service.down"
    await client.aclose()
