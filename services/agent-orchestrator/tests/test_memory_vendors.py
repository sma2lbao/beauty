"""P3-C 厂商适配单测:mem0 / Zep 的请求形状、响应解析与失败归一。

零网络:全部走 httpx.MockTransport;断言的是适配层契约,不验证厂商真实可用性
(那需要部署方的 API key 做 smoke,见 docs/design/memory.md 7.4 节)。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services.memory_remote import MemoryProviderUnavailable
from agent_orchestrator.services.memory_vendors import (
    MEM0_DEFAULT_BASE_URL,
    ZEP_DEFAULT_BASE_URL,
    Mem0MemoryProvider,
    ZepMemoryProvider,
    render_facts,
)
from tests.conftest import _settings

ACCOUNT = "t-vendor"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


# ---- 渲染 ----


def test_render_facts_dedupes_and_respects_budget() -> None:
    block = render_facts(["项目代号是蓝鲸", "项目代号是蓝鲸", "周报用表格"], 1000)
    assert block.splitlines() == [
        memory_service.RECALL_PREFIX,
        "- 项目代号是蓝鲸",
        "- 周报用表格",
    ]
    # 预算只够第一条:标题行保留,后续事实不进块。
    tiny = render_facts(
        ["项目代号是蓝鲸", "周报用表格"], len(memory_service.RECALL_PREFIX) + 10
    )
    assert tiny.splitlines() == [memory_service.RECALL_PREFIX, "- 项目代号是蓝鲸"]
    assert render_facts([], 100) == ""
    assert render_facts(["", "  "], 100) == ""


# ---- mem0 ----


async def test_mem0_recall_request_and_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "m-1", "memory": "用户偏好周报用表格"},
                    {"id": "m-2", "memory": "项目代号是蓝鲸"},
                ]
            },
        )

    client = _client(handler)
    provider = Mem0MemoryProvider(
        MEM0_DEFAULT_BASE_URL, api_key="mem0-key", client=client
    )
    try:
        block = await provider.recall(
            None, ACCOUNT, "周报", workspace_id="w-1", limit=3, max_chars=500
        )
        await provider.aclose()
        assert not client.is_closed
    finally:
        await client.aclose()

    assert block.splitlines() == [
        memory_service.RECALL_PREFIX,
        "- 用户偏好周报用表格",
        "- 项目代号是蓝鲸",
    ]
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/v3/memories/search/"
    assert request.headers["authorization"] == "Token mem0-key"
    assert _body(request) == {
        "query": "周报",
        "filters": {"user_id": ACCOUNT},
        "top_k": 3,
    }


async def test_mem0_recall_empty_results_returns_empty_block() -> None:
    client = _client(lambda request: httpx.Response(200, json={"results": []}))
    provider = Mem0MemoryProvider(api_key="k", client=client)
    try:
        assert await provider.recall(None, ACCOUNT, "周报") == ""
    finally:
        await client.aclose()


async def test_mem0_remember_uses_infer_false_and_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"results": [{"id": "m-9"}]})

    client = _client(handler)
    provider = Mem0MemoryProvider(api_key="mem0-key", client=client)
    try:
        memory_id = await provider.remember(
            None,
            ACCOUNT,
            "项目代号是蓝鲸",
            kind="fact",
            scope="workspace",
            workspace_id="w-1",
            ttl_days=3,
        )
    finally:
        await client.aclose()

    assert memory_id == "m-9"
    body = _body(seen[0])
    assert seen[0].url.path == "/v3/memories/add/"
    assert body["messages"] == [{"role": "user", "content": "项目代号是蓝鲸"}]
    assert body["user_id"] == ACCOUNT
    assert body["infer"] is False
    assert body["metadata"]["kind"] == "fact"
    assert body["metadata"]["workspace_id"] == "w-1"
    assert len(body["expiration_date"]) == 10  # YYYY-MM-DD


async def test_mem0_remember_without_id_is_unavailable() -> None:
    client = _client(lambda request: httpx.Response(200, json={"status": "PENDING"}))
    provider = Mem0MemoryProvider(api_key="k", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.remember(None, ACCOUNT, "项目代号是蓝鲸")
    finally:
        await client.aclose()


async def test_mem0_forget_tolerates_404() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404)

    client = _client(handler)
    provider = Mem0MemoryProvider(api_key="k", client=client)
    try:
        await provider.forget(None, ACCOUNT, "m-1")
    finally:
        await client.aclose()
    assert paths == ["/v1/memories/m-1/"]


async def test_mem0_forget_maps_server_error() -> None:
    client = _client(lambda request: httpx.Response(500, text="boom"))
    provider = Mem0MemoryProvider(api_key="k", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.forget(None, ACCOUNT, "m-1")
    finally:
        await client.aclose()


# ---- Zep ----


async def test_zep_recall_request_and_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "edges": [
                    {"fact": "用户偏好周报用表格"},
                    {"fact": None},
                    {"fact": "项目代号是蓝鲸"},
                ]
            },
        )

    client = _client(handler)
    provider = ZepMemoryProvider(api_key="zep-key", client=client)
    try:
        block = await provider.recall(None, ACCOUNT, "周报", limit=3)
    finally:
        await client.aclose()

    assert block.splitlines()[1:] == ["- 用户偏好周报用表格", "- 项目代号是蓝鲸"]
    request = seen[0]
    assert request.url.host == "api.getzep.com"
    assert request.url.path == "/api/v2/graph/search"
    assert request.headers["authorization"] == "Api-Key zep-key"
    assert _body(request) == {
        "query": "周报",
        "user_id": ACCOUNT,
        "scope": "edges",
        "limit": 3,
    }


async def test_zep_remember_returns_episode_uuid() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"uuid": "ep-1", "content": "x"})

    client = _client(handler)
    provider = ZepMemoryProvider(api_key="zep-key", client=client)
    try:
        episode_id = await provider.remember(
            None, ACCOUNT, "项目代号是蓝鲸", kind="fact", workspace_id="w-1"
        )
    finally:
        await client.aclose()

    assert episode_id == "ep-1"
    body = _body(seen[0])
    assert seen[0].url.path == "/api/v2/graph"
    assert body["data"] == "项目代号是蓝鲸"
    assert body["type"] == "text"
    assert body["user_id"] == ACCOUNT
    assert body["metadata"]["workspace_id"] == "w-1"


async def test_zep_remember_without_uuid_is_unavailable() -> None:
    client = _client(lambda request: httpx.Response(202, json={}))
    provider = ZepMemoryProvider(api_key="k", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.remember(None, ACCOUNT, "项目代号是蓝鲸")
    finally:
        await client.aclose()


async def test_zep_forget_deletes_episode() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = _client(handler)
    provider = ZepMemoryProvider(api_key="k", client=client)
    try:
        await provider.forget(None, ACCOUNT, "ep-1")
    finally:
        await client.aclose()
    assert seen[0].method == "DELETE"
    assert seen[0].url.path == "/api/v2/graph/episodes/ep-1"


async def test_vendor_transport_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    provider = ZepMemoryProvider(api_key="k", client=client)
    try:
        with pytest.raises(MemoryProviderUnavailable):
            await provider.recall(None, ACCOUNT, "周报")
    finally:
        await client.aclose()


# ---- 装配 ----


def test_build_memory_provider_wires_vendors(tmp_path: Path) -> None:
    settings = _settings(tmp_path, "vendor.db")
    assert isinstance(
        memory_service.build_memory_provider(None), memory_service.LocalMemoryProvider
    )
    assert isinstance(
        memory_service.build_memory_provider(settings),
        memory_service.LocalMemoryProvider,
    )

    mem0 = memory_service.build_memory_provider(
        settings.model_copy(
            update={
                "memory_provider": "mem0",
                "memory_vendor_api_key": "k",
                "memory_vendor_base_url": "",
            }
        )
    )
    assert isinstance(mem0, Mem0MemoryProvider)
    assert mem0._base_url == MEM0_DEFAULT_BASE_URL

    zep = memory_service.build_memory_provider(
        settings.model_copy(
            update={
                "memory_provider": "zep",
                "memory_vendor_api_key": "k",
                "memory_vendor_base_url": "http://zep.internal:9000/",
            }
        )
    )
    assert isinstance(zep, ZepMemoryProvider)
    assert zep._base_url == "http://zep.internal:9000"

    # 缺 API key:告警 + 回退本地,不抛异常。
    fallback = memory_service.build_memory_provider(
        settings.model_copy(
            update={"memory_provider": "mem0", "memory_vendor_api_key": ""}
        )
    )
    assert isinstance(fallback, memory_service.LocalMemoryProvider)

    assert ZEP_DEFAULT_BASE_URL == "https://api.getzep.com"
