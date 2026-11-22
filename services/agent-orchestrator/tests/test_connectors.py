"""输入区显式引用的连接器(P1):provider 校验、提示词与工具过滤。

口径(设计 docs/design/composer-context-and-commands.md §7.9):

- 连接器是能力绑定,不进 context_refs;请求里是 provider 列表;
- 空选择 = 不限制(零下游请求),非空选择必须能在工具目录里找到;
- 未知 provider 404 connector.not_found(断连/无工具同码),目录拉取失败保留 502;
- 选中后模型可见工具只剩这些 provider 的连接器工具 + 非连接器工具。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from common.errors import AppError, UpstreamError
from common.tools import ExecutorKind, ToolDescriptor, ToolKind, ToolRisk
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import connectors as connectors_service
from agent_orchestrator.services.tool_client import (
    RemoteToolkit,
    ToolCatalog,
    ToolClient,
)
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from tests.conftest import ACCOUNT, FakeLLM, verifier
from tests.conftest import _settings as base_settings

BASE_URL = "http://tool.local"


def _settings(tmp_path: Path, **overrides: Any):
    return base_settings(tmp_path, "connectors.db", **overrides)


def _descriptor(name: str, *, provider: str | None = None) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"{name} 工具",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        kind=ToolKind.FUNCTION,
        executor=ExecutorKind.CONNECTOR if provider else ExecutorKind.SANDBOX,
        connector={"provider": provider} if provider else None,
    )


class FakeToolClient:
    """工具目录替身:list_tools 返回固定描述,记录调用次数。"""

    def __init__(
        self, tools: list[ToolDescriptor] | None = None, *, fail: bool = False
    ) -> None:
        self.tools = tools if tools is not None else []
        self.fail = fail
        self.calls: list[str] = []

    async def list_tools(self, account_id: str):
        self.calls.append(account_id)
        if self.fail:
            raise UpstreamError("tool.upstream_error", "工具服务不可达")
        return self.tools, "v1"


def _resolve(settings: Any, providers: list[str], tool_client: Any):
    return asyncio.run(
        connectors_service.resolve_connector_providers(
            "t-1", providers, tool_client=tool_client, settings=settings
        )
    )


# ---- 单元:提示词与 provider 校验 ----


def test_connector_reference_note_names_providers() -> None:
    assert connectors_service.connector_reference_note([]) == ""
    note = connectors_service.connector_reference_note(["github", "notion"])
    assert "明确引用了连接器" in note
    assert "github、notion" in note
    assert "未列出的连接器工具不可用" in note


def test_resolve_connector_providers_empty_selection_does_no_work(tmp_path) -> None:
    client = FakeToolClient([_descriptor("github.list_issues", provider="github")])
    assert _resolve(_settings(tmp_path), [], client) == []
    assert client.calls == []


def test_resolve_connector_providers_dedupes_and_rejects_unknown(tmp_path) -> None:
    settings = _settings(tmp_path)
    client = FakeToolClient(
        [
            _descriptor("github.list_issues", provider="github"),
            _descriptor("notion.search", provider="notion"),
            _descriptor("read"),
        ]
    )
    assert _resolve(settings, ["notion", "github", "notion"], client) == [
        "notion",
        "github",
    ]

    with pytest.raises(AppError) as missing:
        _resolve(settings, ["github", "slack"], client)
    assert missing.value.status == 404
    assert missing.value.code == "connector.not_found"
    assert missing.value.details == {"providers": ["slack"]}


def test_resolve_connector_providers_rejects_unavailable_and_limits(tmp_path) -> None:
    with pytest.raises(AppError) as no_client:
        _resolve(_settings(tmp_path), ["github"], None)
    assert no_client.value.status == 409
    assert no_client.value.code == "connector.unavailable"

    with pytest.raises(AppError) as disabled:
        _resolve(
            _settings(tmp_path, connector_providers_enabled=False),
            ["github"],
            FakeToolClient([_descriptor("github.list_issues", provider="github")]),
        )
    assert disabled.value.status == 409
    assert disabled.value.code == "connector.unavailable"

    with pytest.raises(AppError) as too_many:
        _resolve(
            _settings(tmp_path),
            [f"p{index}" for index in range(11)],
            FakeToolClient(),
        )
    assert too_many.value.code == "connector.limit_exceeded"


def test_resolve_connector_providers_keeps_upstream_failure(tmp_path) -> None:
    with pytest.raises(UpstreamError) as upstream:
        _resolve(_settings(tmp_path), ["github"], FakeToolClient(fail=True))
    assert upstream.value.status == 502


# ---- 单元:工具过滤(RemoteToolkit 只暴露被引用的 provider) ----


def _toolkit(*descriptors: ToolDescriptor) -> RemoteToolkit:
    payload = {
        "tools": [item.to_payload() for item in descriptors],
        "catalog_version": "1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = ToolClient(BASE_URL, transport=httpx.MockTransport(handler))
    toolkit = RemoteToolkit("t-1", client=client, catalog=ToolCatalog())
    asyncio.run(toolkit.ensure_ready())
    return toolkit


def test_toolkit_filters_tools_by_selected_connector_providers() -> None:
    toolkit = _toolkit(
        _descriptor("read"),
        _descriptor("github.list_issues", provider="github"),
        _descriptor("notion.search", provider="notion"),
    )

    # 不选 = 全量(既有行为)
    assert sorted(toolkit.names) == ["github.list_issues", "notion.search", "read"]

    with bind_tool_context(
        ToolContext(account_id="t-1", connector_providers=("github",))
    ):
        assert sorted(toolkit.names) == ["github.list_issues", "read"]

    # 空 tuple 与"不选"同义:不限制
    with bind_tool_context(ToolContext(account_id="t-1")):
        assert sorted(toolkit.names) == ["github.list_issues", "notion.search", "read"]


# ---- 接口:消息落库与开流前校验 ----


def _client(settings: Any, tool_client: Any, fake: FakeLLM) -> TestClient:
    return TestClient(
        create_app(
            settings,
            llm_client=fake,
            tool_client=tool_client,
            token_verifier=verifier(),
        )
    )


def test_message_validates_and_persists_connector_providers(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True)
    tool_client = FakeToolClient([_descriptor("github.list_issues", provider="github")])
    fake = FakeLLM(["好的"])
    with _client(settings, tool_client, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]

        unknown = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "查 issue",
                "mode": "chat",
                "connector_providers": ["slack"],
            },
            headers=ACCOUNT,
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "connector.not_found"
        assert (
            client.get(
                f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
            ).json()
            == []
        )

        accepted = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "查 issue",
                "mode": "chat",
                "connector_providers": ["github", "github"],
            },
            headers=ACCOUNT,
        )
        assert accepted.status_code == 201, accepted.text
        messages = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
        ).json()
        assert messages[0]["connector_providers"] == ["github"]
        # 连接器提示词进系统提示
        assert "明确引用了连接器" in fake.calls[0][0]["content"]


def test_task_run_snapshots_connector_providers(tmp_path) -> None:
    settings = _settings(tmp_path, llm_enabled=True)
    tool_client = FakeToolClient([_descriptor("github.list_issues", provider="github")])
    fake = FakeLLM(["# 执行计划\n- [ ] 汇总", "执行结果:完成", "# 交付物\n\n周报"])
    with _client(settings, tool_client, fake) as client:
        conversation = client.post(
            "/api/v1/conversations", json={}, headers=ACCOUNT
        ).json()["id"]

        created = client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={
                "content": "查 issue 并汇总",
                "mode": "task",
                "connector_providers": ["github", "github"],
            },
            headers=ACCOUNT,
        )

        assert created.status_code == 201, created.text
        # 新 run 落快照:后续轮次/子 run 复用同一工具集
        assert created.json()["run"]["connector_providers"] == ["github"]
        messages = client.get(
            f"/api/v1/conversations/{conversation}/messages", headers=ACCOUNT
        ).json()
        assert messages[0]["connector_providers"] == ["github"]
