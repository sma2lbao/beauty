"""连接器工具面:API 目录并集/详情/执行审计(零网络)。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import ACCOUNT
from tests.connectors_support import FakeConnectorGateway, connector_client

# ---- API:目录并集 / 详情 / 执行审计 ------------------------------------


def test_list_tools_merges_connector_tools_for_account(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        resp = client.get("/v1/tools", headers=ACCOUNT)

    assert resp.status_code == 200
    tools = resp.json()["tools"]
    assert [tool["name"] for tool in tools][-1] == "Demo__read_document"
    connector_tool = tools[-1]
    assert connector_tool["executor"] == "connector"
    assert connector_tool["risk"] == "read"
    assert connector_tool["connector"] == {"provider": "demo"}
    assert gateway.tool_calls == ["t-1"]
    # 内置工具的 connector 字段显式为 null,保持 wire 形状稳定。
    assert tools[0]["connector"] is None


def test_list_tools_without_credentials_is_builtin_only(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        anonymous = client.get("/v1/tools")
        account_header_only = client.get("/v1/tools", headers={"X-Account-Id": "t-1"})

    assert anonymous.status_code == 200
    assert "Demo__read_document" not in [
        tool["name"] for tool in anonymous.json()["tools"]
    ]
    # 鉴权开启时 X-Account-Id 不算凭据,仍只返回内置目录。
    assert "Demo__read_document" not in [
        tool["name"] for tool in account_header_only.json()["tools"]
    ]
    assert gateway.tool_calls == []


def test_connector_tools_absent_when_gateway_not_configured(
    client: TestClient,
) -> None:
    resp = client.get("/v1/tools", headers=ACCOUNT)

    assert resp.status_code == 200
    assert "Demo__read_document" not in [tool["name"] for tool in resp.json()["tools"]]


def test_get_connector_tool_requires_account(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        anonymous = client.get("/v1/tools/Demo__read_document")
        found = client.get("/v1/tools/Demo__read_document", headers=ACCOUNT)

    assert anonymous.status_code == 404
    assert anonymous.json()["code"] == "tool.not_found"
    assert found.status_code == 200
    assert found.json()["name"] == "Demo__read_document"
    assert found.json()["connector"] == {"provider": "demo"}


def test_execute_connector_tool_audits_and_returns_envelope(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    gateway.connection_id = "conn-demo-9"
    with connector_client(tmp_path, gateway) as client:
        resp = client.post(
            "/v1/tools/Demo__read_document:execute",
            json={
                "tool_call_id": "call-9",
                "arguments": {"document_id": "doc-1"},
                "run_id": "run-1",
            },
            headers=ACCOUNT,
        )
        calls = client.get("/v1/tool-calls", headers=ACCOUNT)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["content"] == "hello"
    assert body["tool_call_id"] == "call-9"
    assert body["idempotent_replay"] is False
    # connection_id 只进审计,不出现在模型可见的执行信封里。
    assert "connection_id" not in body
    assert gateway.executed == [
        {
            "account_id": "t-1",
            "tool": "Demo__read_document",
            "arguments": {"document_id": "doc-1"},
            "run_id": "run-1",
        }
    ]
    [record] = calls.json()["items"]
    assert record["tool_name"] == "Demo__read_document"
    assert record["risk"] == "read"
    assert record["decision"] == "auto"
    assert record["ok"] is True
    assert record["run_id"] == "run-1"
    assert record["connection_id"] == "conn-demo-9"


def test_execute_unknown_connector_tool_is_404(tmp_path: Path) -> None:
    gateway = FakeConnectorGateway()
    with connector_client(tmp_path, gateway) as client:
        resp = client.post(
            "/v1/tools/Demo__nope:execute",
            json={"tool_call_id": "call-9", "arguments": {}},
            headers=ACCOUNT,
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"
    assert gateway.executed == []
