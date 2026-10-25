"""tool-service 客户端与远端工具箱(设计 §12.3)。

覆盖三件事:HTTP 客户端(目录/执行信封/错误映射)、目录缓存(并发只拉一次、
失败不缓存)、RemoteToolkit/HybridToolkit 的 ToolkitPort 契约(风险映射、别名、
超时、上下文透传、与 MCP 工具合并)。
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
    HybridToolkit,
    RemoteToolkit,
    ToolCatalog,
    ToolClient,
)
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk as LegacyRisk
from agent_orchestrator.services.tools import ToolSpec

BASE_URL = "http://tool.local"
Handler = Callable[[httpx.Request], Any]


def _descriptor(
    name: str = "read",
    *,
    risk: ToolRisk = ToolRisk.READ,
    kind: ToolKind = ToolKind.FUNCTION,
    timeout_seconds: float = 20.0,
    aliases: tuple[str, ...] = ("code_read_file",),
) -> ToolDescriptor:
    """构造一份工具描述(默认只读文件工具,带旧别名)。"""

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


# -------------------------------------------------------------- RemoteToolkit


async def test_toolkit_exposes_only_function_tools() -> None:
    read = _descriptor()
    write = _descriptor("write", risk=ToolRisk.DESTRUCTIVE, aliases=())
    ask = _descriptor("ask_question", kind=ToolKind.INTERACTION, aliases=())
    toolkit, _, client = await _ready(
        _server(_catalog(read, write, ask)), read, write, ask
    )
    assert len(toolkit) == 2
    assert toolkit.names == ["read", "write"]
    assert toolkit.has_dangerous is True
    assert toolkit.contains("read") is True
    assert toolkit.contains("code_read_file") is True
    assert toolkit.contains("ask_question") is False
    schemas = toolkit.schemas()
    assert [item["function"]["name"] for item in schemas] == ["read", "write"]
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["parameters"] == read.input_schema
    await client.aclose()


async def test_toolkit_risk_uses_legacy_values() -> None:
    read = _descriptor()
    write = _descriptor("write", risk=ToolRisk.DESTRUCTIVE, aliases=())
    toolkit, _, client = await _ready(_server(_catalog(read, write)), read, write)
    assert toolkit.risk("read") == "read"
    assert toolkit.risk("code_read_file") == "read"
    assert toolkit.risk("write") == "danger"
    assert toolkit.risk("missing") == "danger"
    await client.aclose()


async def test_toolkit_execute_success_envelope() -> None:
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read)), read)
    payload = json.loads(await toolkit.execute("read", '{"path": "a.txt"}'))
    assert payload["ok"] is True
    assert payload["content"] == "hello"
    assert payload["truncated"] is False
    assert payload["tool_call_id"].startswith("orch-")
    await client.aclose()


async def test_toolkit_execute_alias_hits_canonical_endpoint() -> None:
    seen: list[httpx.Request] = []
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read), seen=seen), read)
    payload = json.loads(await toolkit.execute("code_read_file", '{"path": "a.txt"}'))
    assert payload["ok"] is True
    assert seen[-1].url.path == "/v1/tools/read:execute"
    await client.aclose()


async def test_toolkit_execute_unknown_tool_without_http() -> None:
    seen: list[httpx.Request] = []
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read), seen=seen), read)
    payload = json.loads(await toolkit.execute("missing", "{}"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool.not_found"
    assert [request.method for request in seen] == ["GET"]
    await client.aclose()


async def test_toolkit_execute_interaction_rejected() -> None:
    read = _descriptor()
    ask = _descriptor("ask_question", kind=ToolKind.INTERACTION, aliases=())
    toolkit, _, client = await _ready(_server(_catalog(read, ask)), read, ask)
    payload = json.loads(await toolkit.execute("ask_question", "{}"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool.not_supported"
    await client.aclose()


async def test_toolkit_execute_rejects_bad_arguments() -> None:
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read)), read)
    broken = json.loads(await toolkit.execute("read", "{not json"))
    assert broken["ok"] is False
    assert broken["error"]["code"] == "tool.invalid_args"
    not_object = json.loads(await toolkit.execute("read", "[]"))
    assert not_object["ok"] is False
    assert not_object["error"]["code"] == "tool.invalid_args"
    await client.aclose()


async def test_toolkit_execute_destructive_sets_approved() -> None:
    bodies: list[dict[str, Any]] = []

    def exec_handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_ok_envelope())

    write = _descriptor("write", risk=ToolRisk.DESTRUCTIVE, aliases=())
    read = _descriptor()
    toolkit, _, client = await _ready(
        _server(_catalog(write, read), exec_handler=exec_handler), write, read
    )
    await toolkit.execute("read", '{"path": "a.txt"}')
    await toolkit.execute("write", '{"path": "a.txt"}')
    assert bodies[0]["approved"] is False
    assert bodies[1]["approved"] is True
    await client.aclose()


async def test_toolkit_execute_timeout() -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, json=_ok_envelope())

    read = _descriptor(timeout_seconds=0.01)
    toolkit, _, client = await _ready(
        _server(_catalog(read), exec_handler=slow),
        read,
        timeout_grace_seconds=0.01,
    )
    payload = json.loads(await toolkit.execute("read", '{"path": "a.txt"}'))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool.timeout"
    assert payload["error"]["retryable"] is True
    await client.aclose()


async def test_toolkit_execute_upstream_error_envelope() -> None:
    def exec_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    read = _descriptor()
    toolkit, _, client = await _ready(
        _server(_catalog(read), exec_handler=exec_handler), read
    )
    payload = json.loads(await toolkit.execute("read", '{"path": "a.txt"}'))
    assert payload["ok"] is False
    assert payload["error"]["code"] == UNREACHABLE_CODE
    assert payload["error"]["retryable"] is True
    await client.aclose()


async def test_toolkit_restricted_filters() -> None:
    read = _descriptor()
    write = _descriptor("write", risk=ToolRisk.DESTRUCTIVE, aliases=())
    toolkit, catalog, client = await _ready(_server(_catalog(read, write)), read, write)
    only_read = toolkit.restricted(["code_read_file"])
    assert only_read.names == ["read"]
    assert only_read.risk("write") == "danger"
    assert only_read.risk("read") == "read"
    nothing = toolkit.restricted(["missing"])
    assert len(nothing) == 0
    assert catalog.loaded is True
    await client.aclose()


async def test_toolkit_execute_passes_tool_context() -> None:
    bodies: list[dict[str, Any]] = []

    def exec_handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_ok_envelope())

    read = _descriptor()
    toolkit, _, client = await _ready(
        _server(_catalog(read), exec_handler=exec_handler), read
    )
    context = ToolContext(tenant_id="t-1", run_id="r-1", conversation_id="c-1")
    with bind_tool_context(context):
        await toolkit.execute("read", '{"path": "a.txt"}')
    assert bodies[-1]["run_id"] == "r-1"
    assert bodies[-1]["conversation_id"] == "c-1"
    assert "sandbox_scope" not in bodies[-1]
    await client.aclose()


async def test_toolkit_without_context_omits_ids() -> None:
    bodies: list[dict[str, Any]] = []

    def exec_handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_ok_envelope())

    read = _descriptor()
    toolkit, _, client = await _ready(
        _server(_catalog(read), exec_handler=exec_handler), read
    )
    await toolkit.execute("read", '{"path": "a.txt"}')
    assert "run_id" not in bodies[-1]
    assert "conversation_id" not in bodies[-1]
    await client.aclose()


# -------------------------------------------------------------- HybridToolkit


async def _skill(arguments: dict[str, Any]) -> str:
    return json.dumps({"ok": True, "skill": "run", "arguments": arguments})


def _skill_spec() -> ToolSpec:
    return ToolSpec(
        name="skill_run",
        description="执行技能包",
        parameters={"type": "object", "properties": {}},
        handler=_skill,
        risk=LegacyRisk.READ,
    )


async def test_hybrid_merges_and_routes() -> None:
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read)), read)
    hybrid = toolkit.extended([_skill_spec()])
    assert isinstance(hybrid, HybridToolkit)
    assert len(hybrid) == 2
    assert hybrid.names == ["read", "skill_run"]
    assert [item["function"]["name"] for item in hybrid.schemas()] == [
        "read",
        "skill_run",
    ]
    assert hybrid.risk("skill_run") == "read"
    remote = json.loads(await hybrid.execute("read", '{"path": "a.txt"}'))
    assert remote["ok"] is True
    local = json.loads(await hybrid.execute("skill_run", "{}"))
    assert local["ok"] is True
    assert local["skill"] == "run"
    await client.aclose()


async def test_hybrid_unknown_tool() -> None:
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read)), read)
    hybrid = toolkit.extended([_skill_spec()])
    payload = json.loads(await hybrid.execute("missing", "{}"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "tool.not_found"
    await client.aclose()


async def test_hybrid_restricted() -> None:
    read = _descriptor()
    toolkit, _, client = await _ready(_server(_catalog(read)), read)
    hybrid = toolkit.extended([_skill_spec()])
    only_read = hybrid.restricted(["read"])
    assert only_read.names == ["read"]
    assert only_read.risk("skill_run") == "danger"
    await client.aclose()
