"""工作区:默认工作区幂等、重名冲突、隔离、详情 404。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT


def test_default_workspace_is_idempotent(client: TestClient) -> None:
    first = client.get("/v1/workspaces/default", headers=TENANT)
    assert first.status_code == 200
    body = first.json()
    assert body["name"] == "默认工作区"

    again = client.get("/v1/workspaces/default", headers=TENANT).json()
    assert again["id"] == body["id"]
    assert len(client.get("/v1/workspaces", headers=TENANT).json()) == 1


def test_create_list_and_duplicate(client: TestClient) -> None:
    created = client.post("/v1/workspaces", json={"name": "销售资料"}, headers=TENANT)
    assert created.status_code == 201
    workspace = created.json()

    assert [
        item["id"] for item in client.get("/v1/workspaces", headers=TENANT).json()
    ] == [workspace["id"]]

    duplicate = client.post("/v1/workspaces", json={"name": "销售资料"}, headers=TENANT)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "workspace.exists"


def test_workspace_isolation_and_missing(client: TestClient) -> None:
    workspace_id = client.post(
        "/v1/workspaces", json={"name": "销售资料"}, headers=TENANT
    ).json()["id"]

    assert client.get("/v1/workspaces", headers=OTHER).json() == []
    assert (
        client.get(f"/v1/workspaces/{workspace_id}", headers=OTHER).status_code == 404
    )
    assert (
        client.get("/v1/workspaces/nope", headers=TENANT).json()["code"]
        == "workspace.not_found"
    )
