"""tool-service 客户端与远端工具箱(设计 §12.3)。

覆盖三件事:HTTP 客户端(目录/执行信封/错误映射)、目录缓存(并发只拉一次、
失败不缓存)、RemoteToolkit/HybridToolkit 的 ToolkitPort 契约(风险映射、别名、
超时、上下文透传、与 MCP 工具合并)。
本文件承载原 ``test_tool_client.py`` 拆分后的ToolClient HTTP 客户端与目录缓存部分。
"""

from __future__ import annotations

import asyncio
import json
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


# ---------------------------------------------------------------- ToolClient


async def test_list_tools_parses_catalog() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_catalog(_descriptor(), version="7"))

    client = _client(handler)
    tools, version = await client.list_tools()
    assert [tool.name for tool in tools] == ["read"]
    assert version == "7"
    assert seen[0].url.path == "/v1/tools"
    assert "authorization" not in seen[0].headers
    await client.aclose()


async def test_list_tools_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tools()
    assert exc_info.value.code == UNREACHABLE_CODE
    await client.aclose()


async def test_list_tools_non_json_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tools()
    assert exc_info.value.code == UPSTREAM_ERROR_CODE
    await client.aclose()


async def test_list_tools_missing_tools_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"catalog_version": "1"})

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tools()
    assert exc_info.value.code == UPSTREAM_ERROR_CODE
    assert "tools" in exc_info.value.message
    await client.aclose()


async def test_list_tools_invalid_descriptor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tools": [{"name": "read"}]})

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tools()
    assert exc_info.value.code == UPSTREAM_ERROR_CODE
    assert "目录响应非法" in exc_info.value.message
    await client.aclose()


async def test_list_tools_http_error_uses_service_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"code": "tool_service.down", "message": "维护中"}
        )

    client = _client(handler)
    with pytest.raises(UpstreamError) as exc_info:
        await client.list_tools()
    assert exc_info.value.code == "tool_service.down"
    await client.aclose()


async def test_execute_injects_bearer_and_payload() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_envelope())

    client = _client(handler, token_provider=_token)
    from common.tools import ToolExecuteRequest

    request = ToolExecuteRequest(tool_call_id="call-1", arguments={"path": "a.txt"})
    response = await client.execute("t-9", "read", request)
    assert response.ok is True
    assert response.result is not None
    assert response.result["content"] == "hello"
    assert seen[0].headers["authorization"] == "Bearer tok-t-9"
    assert seen[0].url.path == "/v1/tools/read:execute"
    body = json.loads(seen[0].content)
    assert body["tool_call_id"] == "call-1"
    assert body["arguments"] == {"path": "a.txt"}
    assert body["approved"] is False
    await client.aclose()


async def test_execute_passes_approved_and_tool_call_id() -> None:
    """P1:approved 由调用方决定,原样进请求体(不再按 risk 自动置 True)。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_envelope())

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    request = ToolExecuteRequest(
        tool_call_id="call-9", arguments={"path": "a.txt"}, approved=True
    )
    await client.execute("t-1", "write", request)
    body = json.loads(seen[0].content)
    assert body["approved"] is True
    assert body["tool_call_id"] == "call-9"
    await client.aclose()


async def test_execute_http_error_maps_to_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "code": "tool.confirmation_required",
                "message": "需要人工确认",
                "details": {},
            },
        )

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    response = await client.execute(
        "t-1", "write", ToolExecuteRequest(tool_call_id="call-2", arguments={})
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.confirmation_required"
    assert response.error.retryable is False
    assert response.tool_call_id == "call-2"
    await client.aclose()


async def test_execute_http_5xx_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": "tool.io_error", "message": "炸了"})

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    response = await client.execute(
        "t-1", "read", ToolExecuteRequest(tool_call_id="call-3", arguments={})
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "tool.io_error"
    assert response.error.retryable is True
    await client.aclose()


async def test_execute_http_error_without_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    response = await client.execute(
        "t-1", "read", ToolExecuteRequest(tool_call_id="call-4", arguments={})
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == UPSTREAM_ERROR_CODE
    assert response.error.retryable is True
    await client.aclose()


async def test_execute_non_json_200_is_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="oops")

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    response = await client.execute(
        "t-1", "read", ToolExecuteRequest(tool_call_id="call-5", arguments={})
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == UPSTREAM_ERROR_CODE
    assert "非 JSON" in response.error.message
    await client.aclose()


async def test_execute_invalid_envelope_200_is_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": "缺 ok"})

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    response = await client.execute(
        "t-1", "read", ToolExecuteRequest(tool_call_id="call-6", arguments={})
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == UPSTREAM_ERROR_CODE
    assert "信封非法" in response.error.message
    await client.aclose()


async def test_execute_transport_error_raises_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = _client(handler)
    from common.tools import ToolExecuteRequest

    with pytest.raises(UpstreamError) as exc_info:
        await client.execute(
            "t-1", "read", ToolExecuteRequest(tool_call_id="call-7", arguments={})
        )
    assert exc_info.value.code == UNREACHABLE_CODE
    await client.aclose()


async def test_execute_token_failure_raises_upstream() -> None:
    async def broken(tenant_id: str) -> str:
        raise RuntimeError("no secret")

    client = _client(
        lambda request: httpx.Response(200, json=_ok_envelope()), token_provider=broken
    )
    from common.tools import ToolExecuteRequest

    with pytest.raises(UpstreamError) as exc_info:
        await client.execute(
            "t-1", "read", ToolExecuteRequest(tool_call_id="call-8", arguments={})
        )
    assert exc_info.value.code == UNREACHABLE_CODE
    assert "token" in exc_info.value.message
    await client.aclose()


# --------------------------------------------------------------- ToolCatalog


async def test_catalog_ensure_loads_once_concurrently() -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_catalog(_descriptor()))

    client = _client(handler)
    catalog = ToolCatalog()
    await asyncio.gather(catalog.ensure(client), catalog.ensure(client))
    assert calls["n"] == 1
    assert catalog.loaded is True
    assert catalog.version == "1"
    assert [tool.name for tool in catalog.descriptors] == ["read"]
    await client.aclose()


async def test_catalog_failure_not_cached() -> None:
    state = {"fail": True, "n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["fail"]:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_catalog(_descriptor()))

    client = _client(handler)
    catalog = ToolCatalog()
    with pytest.raises(UpstreamError):
        await catalog.ensure(client)
    assert catalog.loaded is False
    state["fail"] = False
    await catalog.ensure(client)
    assert catalog.loaded is True
    assert state["n"] == 2
    await client.aclose()


async def test_catalog_refresh_reloads() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200, json=_catalog(_descriptor(), version=str(calls["n"]))
        )

    client = _client(handler)
    catalog = ToolCatalog()
    await catalog.ensure(client)
    assert catalog.version == "1"
    await catalog.refresh(client)
    assert catalog.version == "2"
    assert calls["n"] == 2
    await client.aclose()
