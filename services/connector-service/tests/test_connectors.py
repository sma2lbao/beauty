"""连接器目录与内部工具装配:账号隔离 + 内部密钥门。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import ACCOUNT, INTERNAL_HEADERS, OTHER, insert_connection


def test_catalog_lists_demo(client: TestClient) -> None:
    resp = client.get("/v1/connectors", headers=ACCOUNT)
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert [p["name"] for p in providers] == ["demo"]
    demo = providers[0]
    assert demo["display_name"] == "示例连接器"
    assert demo["configured"] is True
    assert demo["connected"] is False
    assert demo["connection"] is None
    assert "offline_access" in demo["scopes"]
    assert demo["docs_url"].startswith("https://")
    names = [tool["name"] for tool in demo["tools"]]
    assert names == [
        "Demo__get_me",
        "Demo__list_files",
        "Demo__read_document",
        "Demo__list_document_blocks",
        "Demo__list_wiki_spaces",
        "Demo__list_wiki_nodes",
    ]
    assert all(tool["risk"] == "read" for tool in demo["tools"])


def test_catalog_reflects_connection_and_account_scope(
    client: TestClient, tmp_path: Path
) -> None:
    conn = insert_connection(tmp_path / "test.db")
    mine = client.get("/v1/connectors", headers=ACCOUNT).json()["providers"][0]
    assert mine["connected"] is True
    assert mine["connection"]["id"] == conn["id"]
    assert mine["connection"]["status"] == "active"
    assert mine["connection"]["enabled"] is True

    theirs = client.get("/v1/connectors", headers=OTHER).json()["providers"][0]
    assert theirs["connected"] is False


def test_internal_tools_requires_secret(client: TestClient) -> None:
    resp = client.get("/v1/internal/connectors/tools", headers={"X-Account-Id": "t-1"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "connector.internal_forbidden"
    wrong = client.get(
        "/v1/internal/connectors/tools",
        headers={"X-Internal-Secret": "nope", "X-Account-Id": "t-1"},
    )
    assert wrong.status_code == 401


def test_internal_tools_only_for_connected_account(
    client: TestClient, tmp_path: Path
) -> None:
    empty = client.get("/v1/internal/connectors/tools", headers=INTERNAL_HEADERS)
    assert empty.status_code == 200
    assert empty.json() == {"tools": []}

    insert_connection(tmp_path / "test.db")
    tools = client.get("/v1/internal/connectors/tools", headers=INTERNAL_HEADERS).json()
    names = [tool["name"] for tool in tools["tools"]]
    assert names == [
        "Demo__get_me",
        "Demo__list_files",
        "Demo__read_document",
        "Demo__list_document_blocks",
        "Demo__list_wiki_spaces",
        "Demo__list_wiki_nodes",
    ]
    descriptor = tools["tools"][2]
    assert descriptor["executor"] == "connector"
    # tool-service 靠这个字段把执行路由回本 provider(见 ConnectorGateway)
    assert descriptor["connector"] == {"provider": "demo"}
    assert descriptor["risk"] == "read"
    assert descriptor["input_schema"]["required"] == ["document_id"]
    # 其他账号看不到
    other = client.get(
        "/v1/internal/connectors/tools",
        headers={
            "X-Internal-Secret": INTERNAL_HEADERS["X-Internal-Secret"],
            "X-Account-Id": "t-2",
        },
    )
    assert other.json() == {"tools": []}


def test_disabled_connection_not_exposed(client: TestClient, tmp_path: Path) -> None:
    insert_connection(tmp_path / "test.db", enabled=False)
    catalog = client.get("/v1/connectors", headers=ACCOUNT).json()["providers"][0]
    assert catalog["connected"] is False
    tools = client.get("/v1/internal/connectors/tools", headers=INTERNAL_HEADERS).json()
    assert tools["tools"] == []
