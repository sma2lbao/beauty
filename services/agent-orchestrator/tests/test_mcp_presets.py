"""MCP 预设连接器:预设元数据、一键幂等创建与工具粒度风险分级。"""

from __future__ import annotations

import asyncio

import pytest
from common.errors import NotFoundError
from fastapi.testclient import TestClient

from agent_orchestrator.models.mcp import MCPServer
from agent_orchestrator.services import mcp as mcp_service
from agent_orchestrator.services import mcp_runtime
from agent_orchestrator.services.mcp import _tool_risk
from agent_orchestrator.services.mcp_presets import (
    NOTION_MCP_URL,
    list_presets,
    preset_for_key,
    preset_for_url,
)
from agent_orchestrator.services.tools import ToolRisk
from tests.conftest import ACCOUNT

OTHER_MCP_URL = "http://mcp.example.com/mcp"


def _server(url: str = OTHER_MCP_URL, *, auto_approve: bool = False) -> MCPServer:
    return MCPServer(
        account_id="t",
        name="Demo",
        url=url,
        enabled=True,
        auto_approve=auto_approve,
        tools_allowlist=[],
        tools_cache=[],
        timeout_seconds=2,
    )


class AnnotatedClient:
    """只实现 tools/list 的假客户端:带 annotations 的工具 + 一个非法 annotations。"""

    async def initialize(self) -> dict:
        return {}

    async def list_tools(self) -> list[dict]:
        return [
            {
                "name": "echo",
                "description": "回显",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True, "title": "Echo"},
            },
            {"name": "weird", "inputSchema": {}, "annotations": "not-a-dict"},
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        raise AssertionError("预设/缓存测试不应真正调用工具")

    async def aclose(self) -> None:
        return None


def test_preset_catalog_shape() -> None:
    presets = list_presets()
    assert [preset.key for preset in presets] == ["notion"]
    notion = preset_for_key("notion")
    assert notion.url == NOTION_MCP_URL == "https://mcp.notion.com/mcp"
    assert notion.docs_url
    assert "notion-search" in notion.read_prefixes
    assert "notion-create-pages" not in notion.read_prefixes
    # 反查忽略首尾空白与末尾斜杠
    assert preset_for_url(f"  {NOTION_MCP_URL}/ ") is notion
    assert preset_for_url(OTHER_MCP_URL) is None
    assert preset_for_url(None) is None
    assert preset_for_url("") is None


def test_preset_for_unknown_key_raises_not_found() -> None:
    with pytest.raises(NotFoundError) as caught:
        preset_for_key("slack")
    assert caught.value.code == "mcp.preset_not_found"


def test_list_presets_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/mcp/presets", headers=ACCOUNT)
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["presets"]
    assert [item["key"] for item in body["presets"]] == ["notion"]
    item = body["presets"][0]
    assert item["name"] == "Notion"
    assert item["url"] == NOTION_MCP_URL
    assert item["docs_url"] == "https://developers.notion.com/docs/mcp"
    assert item["description"]
    # read_prefixes 是服务端内部的风险分级信息,不应外露
    assert "read_prefixes" not in item


def test_ensure_preset_server_is_idempotent(client: TestClient) -> None:
    first = client.post("/api/v1/mcp/presets/notion", headers=ACCOUNT)
    assert first.status_code == 200
    created = first.json()
    assert created["name"] == "Notion"
    assert created["transport"] == "http"
    assert created["url"] == NOTION_MCP_URL
    assert created["auto_approve"] is False
    assert created["tools_allowlist"] == []
    assert created["tools"] == []

    second = client.post("/api/v1/mcp/presets/notion", headers=ACCOUNT)
    assert second.status_code == 200
    assert second.json()["id"] == created["id"]
    listing = client.get("/api/v1/mcp/servers", headers=ACCOUNT).json()
    assert len(listing) == 1


def test_ensure_preset_reuses_server_with_same_url(client: TestClient) -> None:
    existing = client.post(
        "/api/v1/mcp/servers",
        json={"name": "MyNotion", "url": NOTION_MCP_URL},
        headers=ACCOUNT,
    )
    assert existing.status_code == 201
    ensured = client.post("/api/v1/mcp/presets/notion", headers=ACCOUNT)
    assert ensured.status_code == 200
    assert ensured.json()["id"] == existing.json()["id"]
    assert ensured.json()["name"] == "MyNotion"


def test_ensure_preset_server_name_falls_back(client: TestClient) -> None:
    taken = client.post(
        "/api/v1/mcp/servers",
        json={"name": "Notion", "url": OTHER_MCP_URL},
        headers=ACCOUNT,
    )
    assert taken.status_code == 201
    ensured = client.post("/api/v1/mcp/presets/notion", headers=ACCOUNT)
    assert ensured.status_code == 200
    # name 只允许 [A-Za-z0-9._-],所以回退成 Notion-2 而不是 "Notion (2)"
    assert ensured.json()["name"] == "Notion-2"


def test_ensure_preset_unknown_key_404(client: TestClient) -> None:
    response = client.post("/api/v1/mcp/presets/slack", headers=ACCOUNT)
    assert response.status_code == 404
    assert response.json()["code"] == "mcp.preset_not_found"


@pytest.mark.parametrize(
    ("url", "tool_name", "annotations", "auto_approve", "expected"),
    [
        # 1. destructiveHint 压过 readOnlyHint
        (
            NOTION_MCP_URL,
            "notion-search",
            {"destructiveHint": True, "readOnlyHint": True},
            False,
            ToolRisk.DANGER,
        ),
        # 2. readOnlyHint → READ
        (
            NOTION_MCP_URL,
            "notion-create-pages",
            {"readOnlyHint": True},
            False,
            ToolRisk.READ,
        ),
        # 3. 预设 url + 只读前缀 → READ(server 没声明 annotations 时的兜底)
        (NOTION_MCP_URL, "notion-search", {}, False, ToolRisk.READ),
        # 4. 预设 url 的写工具 + 账号信任 → WRITE
        (NOTION_MCP_URL, "notion-create-pages", {}, True, ToolRisk.WRITE),
        # 5. 非预设 server + 信任 → WRITE
        (OTHER_MCP_URL, "echo", {}, True, ToolRisk.WRITE),
        # 6. 非预设 server 的只读注解 → READ
        (OTHER_MCP_URL, "echo", {"readOnlyHint": True}, False, ToolRisk.READ),
        # 7. 非预设 server 的破坏性注解 → DANGER
        (OTHER_MCP_URL, "echo", {"destructiveHint": True}, True, ToolRisk.DANGER),
        # 8. 无注解 + 未信任 → DANGER(对话路径没有审批流)
        (OTHER_MCP_URL, "echo", {}, False, ToolRisk.DANGER),
    ],
)
def test_tool_risk_granularity(
    url: str,
    tool_name: str,
    annotations: dict,
    auto_approve: bool,
    expected: ToolRisk,
) -> None:
    wrapped = mcp_runtime._wrap_tool(
        {"name": tool_name, "inputSchema": {}, "annotations": annotations}
    )
    server = _server(url, auto_approve=auto_approve)
    assert _tool_risk(server, wrapped) is expected


def test_wrap_tool_ignores_non_dict_annotations() -> None:
    wrapped = mcp_runtime._wrap_tool({"name": "echo", "annotations": "nope"})
    assert wrapped.annotations == {}


def test_tool_specs_risk_from_annotations(monkeypatch) -> None:
    server = _server()
    server.tools_cache = [
        {
            "name": "echo",
            "description": "",
            "inputSchema": {},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "danger",
            "description": "",
            "inputSchema": {},
            "annotations": {"destructiveHint": True},
        },
        {"name": "write", "description": "", "inputSchema": {}},
    ]

    class FakeRepo:
        def __init__(self, session) -> None:
            pass

        async def list_scoped(self, *args, **kwargs):
            return [server]

    monkeypatch.setattr(mcp_service, "MCPServerRepository", FakeRepo)

    async def scenario() -> list:
        return await mcp_service.tool_specs(  # type: ignore[arg-type]
            None, "t", secret="x"
        )

    specs = asyncio.run(scenario())
    risks = {spec.name: spec.risk for spec in specs}
    assert risks == {
        "mcp__demo__echo": ToolRisk.READ,
        "mcp__demo__danger": ToolRisk.DANGER,
        "mcp__demo__write": ToolRisk.DANGER,
    }
    confirmations = {spec.name: spec.requires_confirmation for spec in specs}
    assert confirmations["mcp__demo__echo"] is False
    assert confirmations["mcp__demo__write"] is True


def test_test_endpoint_persists_annotations(client: TestClient) -> None:
    created = client.post(
        "/api/v1/mcp/servers",
        json={"name": "annotated", "url": OTHER_MCP_URL},
        headers=ACCOUNT,
    )
    assert created.status_code == 201
    original = mcp_runtime.build_client
    mcp_runtime.build_client = lambda *args, **kwargs: AnnotatedClient()  # type: ignore[assignment]
    try:
        tested = client.post(
            f"/api/v1/mcp/servers/{created.json()['id']}/test", headers=ACCOUNT
        )
        assert tested.status_code == 200
        tools = tested.json()["tools"]
        assert tools[0]["annotations"] == {"readOnlyHint": True, "title": "Echo"}
        # 非 dict 的 annotations 按空对象处理,不让脏数据进缓存
        assert tools[1]["annotations"] == {}

        cached = client.get("/api/v1/mcp/servers", headers=ACCOUNT).json()[0]["tools"]
        assert cached[0]["annotations"] == {"readOnlyHint": True, "title": "Echo"}
    finally:
        mcp_runtime.build_client = original  # type: ignore[assignment]


def test_refresh_tools_writes_annotations_into_cache() -> None:
    server = _server()
    original = mcp_runtime.build_client
    mcp_runtime.build_client = lambda *args, **kwargs: AnnotatedClient()  # type: ignore[assignment]
    try:

        async def scenario() -> list:
            return await mcp_runtime.refresh_tools(server, "secret", token="token")

        tools = asyncio.run(scenario())
    finally:
        mcp_runtime.build_client = original  # type: ignore[assignment]
    assert tools[0].annotations == {"readOnlyHint": True, "title": "Echo"}
    assert server.tools_cache[0]["annotations"] == {
        "readOnlyHint": True,
        "title": "Echo",
    }
    assert server.tools_cache[1]["annotations"] == {}
