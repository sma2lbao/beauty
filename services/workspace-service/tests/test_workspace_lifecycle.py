"""工作区生命周期:改名/清空描述、重名冲突、删除级联交付物、默认工作区保护。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT


def _create_workspace(
    client: TestClient, name: str, description: str | None = None
) -> dict:
    body: dict[str, object] = {"name": name}
    if description is not None:
        body["description"] = description
    created = client.post("/v1/workspaces", json=body, headers=TENANT)
    assert created.status_code == 201
    return created.json()


def _create_artifact_in(
    client: TestClient, workspace_id: str, title: str = "周报"
) -> dict:
    created = client.post(
        "/v1/artifacts",
        json={"title": title, "content": "# 周报\n要点", "workspace_id": workspace_id},
        headers=TENANT,
    )
    assert created.status_code == 201
    return created.json()


def test_rename_updates_name_and_description(client: TestClient) -> None:
    workspace = _create_workspace(client, "销售资料", "华东区")

    patched = client.patch(
        f"/v1/workspaces/{workspace['id']}",
        json={"name": "销售资料库", "description": "全国区"},
        headers=TENANT,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["id"] == workspace["id"]
    assert body["name"] == "销售资料库"
    assert body["description"] == "全国区"
    assert body["is_default"] is False

    fetched = client.get(f"/v1/workspaces/{workspace['id']}", headers=TENANT).json()
    assert fetched["name"] == "销售资料库"
    assert fetched["description"] == "全国区"
    listed = client.get("/v1/workspaces", headers=TENANT).json()
    assert [item["name"] for item in listed] == ["销售资料库"]


def test_rename_only_keeps_description_and_null_clears_it(client: TestClient) -> None:
    workspace = _create_workspace(client, "销售资料", "华东区")

    renamed = client.patch(
        f"/v1/workspaces/{workspace['id']}",
        json={"name": "销售资料库"},
        headers=TENANT,
    ).json()
    assert renamed["description"] == "华东区"  # 只改名不动描述

    cleared = client.patch(
        f"/v1/workspaces/{workspace['id']}",
        json={"description": None},
        headers=TENANT,
    ).json()
    assert cleared["name"] == "销售资料库"
    assert cleared["description"] is None  # 显式 null 清空

    # 空 body 什么都不改(仍 200)
    unchanged = client.patch(
        f"/v1/workspaces/{workspace['id']}", json={}, headers=TENANT
    ).json()
    assert unchanged["name"] == "销售资料库"
    assert unchanged["description"] is None


def test_rename_to_existing_name_conflicts(client: TestClient) -> None:
    first = _create_workspace(client, "销售资料")
    second = _create_workspace(client, "市场资料")

    response = client.patch(
        f"/v1/workspaces/{first['id']}",
        json={"name": "市场资料"},
        headers=TENANT,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "workspace.exists"
    assert body["details"] == {"workspace_id": second["id"], "name": "市场资料"}

    # 改成自己原来的名字不算冲突
    same = client.patch(
        f"/v1/workspaces/{first['id']}",
        json={"name": "销售资料"},
        headers=TENANT,
    )
    assert same.status_code == 200


def test_rename_and_delete_are_tenant_isolated(client: TestClient) -> None:
    workspace = _create_workspace(client, "销售资料")

    assert (
        client.patch(
            f"/v1/workspaces/{workspace['id']}",
            json={"name": "被别人改"},
            headers=OTHER,
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/v1/workspaces/{workspace['id']}", headers=OTHER).status_code
        == 404
    )
    # 跨租户的 404 与不存在不可区分,且没有真的改动
    missing = client.patch("/v1/workspaces/nope", json={"name": "x"}, headers=TENANT)
    assert missing.status_code == 404
    assert missing.json()["code"] == "workspace.not_found"
    assert (
        client.delete("/v1/workspaces/nope", headers=TENANT).json()["code"]
        == "workspace.not_found"
    )
    assert (
        client.get(f"/v1/workspaces/{workspace['id']}", headers=TENANT).json()["name"]
        == "销售资料"
    )


def test_delete_workspace_cascades_artifacts(client: TestClient, tmp_path) -> None:
    workspace = _create_workspace(client, "临时资料")
    artifact = _create_artifact_in(client, workspace["id"])
    root = tmp_path / "objects"
    files = [path for path in root.rglob("*") if path.is_file()]
    assert len(files) == 1

    deleted = client.delete(f"/v1/workspaces/{workspace['id']}", headers=TENANT)
    assert deleted.status_code == 204

    assert (
        client.get(f"/v1/workspaces/{workspace['id']}", headers=TENANT).status_code
        == 404
    )
    assert workspace["id"] not in [
        item["id"] for item in client.get("/v1/workspaces", headers=TENANT).json()
    ]
    assert (
        client.get(f"/v1/artifacts/{artifact['id']}", headers=TENANT).status_code == 404
    )
    assert [path for path in root.rglob("*") if path.is_file()] == []


def test_delete_keeps_other_workspace_artifacts(client: TestClient, tmp_path) -> None:
    kept = _create_workspace(client, "保留资料")
    doomed = _create_workspace(client, "临时资料")
    kept_artifact = _create_artifact_in(client, kept["id"], title="保留")
    _create_artifact_in(client, doomed["id"], title="删除")

    assert (
        client.delete(f"/v1/workspaces/{doomed['id']}", headers=TENANT).status_code
        == 204
    )

    assert (
        client.get(f"/v1/artifacts/{kept_artifact['id']}", headers=TENANT).status_code
        == 200
    )
    root = tmp_path / "objects"
    assert len([path for path in root.rglob("*") if path.is_file()]) == 1


def test_default_workspace_is_protected_but_renameable(client: TestClient) -> None:
    default = client.get("/v1/workspaces/default", headers=TENANT).json()
    assert default["is_default"] is True

    protected = client.delete(f"/v1/workspaces/{default['id']}", headers=TENANT)
    assert protected.status_code == 409
    body = protected.json()
    assert body["code"] == "workspace.default_protected"
    assert body["details"] == {"workspace_id": default["id"]}

    renamed = client.patch(
        f"/v1/workspaces/{default['id']}",
        json={"name": "团队资料"},
        headers=TENANT,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "团队资料"
    assert renamed.json()["is_default"] is True

    # 改名后仍然是同一个默认工作区(不会因为按名称找不到而重复建档)
    again = client.get("/v1/workspaces/default", headers=TENANT).json()
    assert again["id"] == default["id"]
    assert again["name"] == "团队资料"
    # 交付物不传 workspace_id 时仍落这个(已改名的)默认工作区
    artifact = client.post(
        "/v1/artifacts", json={"title": "周报", "content": "# 周报"}, headers=TENANT
    )
    assert artifact.status_code == 201
    assert artifact.json()["workspace_id"] == default["id"]
    assert len(client.get("/v1/workspaces", headers=TENANT).json()) == 1


def test_list_workspaces_exposes_is_default(client: TestClient) -> None:
    default = client.get("/v1/workspaces/default", headers=TENANT).json()
    other = _create_workspace(client, "销售资料")

    listed = client.get("/v1/workspaces", headers=TENANT).json()
    by_id = {item["id"]: item for item in listed}
    assert by_id[default["id"]]["is_default"] is True
    assert by_id[other["id"]]["is_default"] is False
    assert by_id[other["id"]]["name"] == "销售资料"
