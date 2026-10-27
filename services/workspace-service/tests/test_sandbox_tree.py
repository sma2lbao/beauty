"""执行现场:沙箱文件树列举(GET /v1/sandboxes/{id}/tree)与条目契约。

零网络:provider 用 conftest 的 FakeSandboxProvider,树条目由测试预置。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from common.errors import NotFoundError, UpstreamError
from database.session import session_scope
from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeSandboxProvider
from workspace.models.sandbox import SandboxInstance
from workspace.schemas.sandbox import MAX_TREE_ENTRIES


def _create(client: TestClient, **payload: Any) -> dict[str, Any]:
    response = client.post("/v1/sandboxes", json=payload, headers=TENANT)
    assert response.status_code == 201, response.text
    return response.json()


def _tree(
    client: TestClient,
    sandbox_id: str,
    *,
    headers: dict[str, str] | None = None,
    **params: Any,
) -> Any:
    return client.get(
        f"/v1/sandboxes/{sandbox_id}/tree",
        params=params,
        headers=TENANT if headers is None else headers,
    )


async def _set_expired(client: TestClient, sandbox_id: str) -> None:
    factory = client.app.state.session_factory
    async with session_scope(factory) as session:
        record = await session.get(SandboxInstance, sandbox_id)
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=5)


def test_tree_lists_entries_of_workspace(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    sandbox_provider.add_entry(
        sandbox["name"], "/workspace/main.py", "file", size=12, modified=1.5
    )
    sandbox_provider.add_entry(sandbox["name"], "/workspace/src", "dir")
    sandbox_provider.add_entry(sandbox["name"], "/workspace/src/app.ts", "file")

    response = _tree(client, sandbox["id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == "/workspace"
    assert body["truncated"] is False
    # depth 默认 1:只有直接子项,按路径排序
    assert body["entries"] == [
        {
            "path": "/workspace/main.py",
            "name": "main.py",
            "kind": "file",
            "size_bytes": 12,
            "modified": 1.5,
        },
        {
            "path": "/workspace/src",
            "name": "src",
            "kind": "dir",
            "size_bytes": 0,
            "modified": None,
        },
    ]
    assert sandbox_provider.list_calls == [(sandbox["name"], "/workspace", 1)]


def test_tree_depth_expands_nested_entries(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    name = sandbox["name"]
    sandbox_provider.add_entry(name, "/workspace/src", "dir")
    sandbox_provider.add_entry(name, "/workspace/src/app.ts", "file", size=3)
    sandbox_provider.add_entry(name, "/workspace/src/nested", "dir")
    sandbox_provider.add_entry(name, "/workspace/src/nested/deep.ts", "file", size=1)

    shallow = _tree(client, sandbox["id"], depth=1, path="/workspace/src").json()
    assert [item["path"] for item in shallow["entries"]] == [
        "/workspace/src/app.ts",
        "/workspace/src/nested",
    ]

    deeper = _tree(client, sandbox["id"], depth=3, path="/workspace/src").json()
    assert [item["path"] for item in deeper["entries"]] == [
        "/workspace/src/app.ts",
        "/workspace/src/nested",
        "/workspace/src/nested/deep.ts",
    ]
    assert deeper["path"] == "/workspace/src"
    assert sandbox_provider.list_calls[-1] == (name, "/workspace/src", 3)


def test_tree_truncates_beyond_max_entries(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    for index in range(MAX_TREE_ENTRIES + 1):
        sandbox_provider.add_entry(
            sandbox["name"], f"/workspace/f{index:04d}.txt", "file", size=index
        )

    body = _tree(client, sandbox["id"]).json()

    assert body["truncated"] is True
    assert len(body["entries"]) == MAX_TREE_ENTRIES
    assert body["entries"][0]["name"] == "f0000.txt"
    assert body["entries"][-1]["name"] == f"f{MAX_TREE_ENTRIES - 1:04d}.txt"


def test_tree_empty_dir(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)

    response = _tree(client, sandbox["id"])

    assert response.status_code == 200
    assert response.json() == {
        "path": "/workspace",
        "entries": [],
        "truncated": False,
    }


def test_tree_is_tenant_isolated(client: TestClient) -> None:
    sandbox = _create(client)

    response = _tree(client, sandbox["id"], headers=OTHER)

    assert response.status_code == 404
    assert response.json()["code"] == "sandbox.not_found"
    assert _tree(client, sandbox["id"]).status_code == 200


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "/workspace/../etc/passwd", "workspace/a"]
)
def test_tree_rejects_invalid_path(
    client: TestClient, sandbox_provider: FakeSandboxProvider, path: str
) -> None:
    sandbox = _create(client)

    response = _tree(client, sandbox["id"], path=path)

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "sandbox.path_invalid"
    assert sandbox_provider.list_calls == []


def test_tree_query_bounds(client: TestClient) -> None:
    sandbox = _create(client)
    assert _tree(client, sandbox["id"], path="").status_code == 422
    assert _tree(client, sandbox["id"], depth=0).status_code == 422
    assert _tree(client, sandbox["id"], depth=5).status_code == 422


def test_tree_provider_unavailable_is_502(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    sandbox_provider.list_error = UpstreamError(
        "sandbox.provider_unavailable", "沙箱运行时不可用"
    )

    response = _tree(client, sandbox["id"])

    assert response.status_code == 502
    assert response.json()["code"] == "sandbox.provider_unavailable"


def test_tree_provider_error_is_502(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    sandbox_provider.fail_list = True

    response = _tree(client, sandbox["id"])

    assert response.status_code == 502
    assert response.json()["code"] == "sandbox.provider_error"


def test_tree_missing_dir_is_404(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    sandbox_provider.list_error = NotFoundError(
        "sandbox.file_not_found", "沙箱内目录不存在:/workspace/nope"
    )

    response = _tree(client, sandbox["id"], path="/workspace/nope")

    assert response.status_code == 404
    assert response.json()["code"] == "sandbox.file_not_found"


async def test_tree_on_expired_sandbox_conflicts(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox = _create(client)
    await _set_expired(client, sandbox["id"])

    response = _tree(client, sandbox["id"])

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox.expired"
    assert sandbox_provider.destroyed == [sandbox["name"]]
