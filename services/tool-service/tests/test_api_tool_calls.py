"""审计查询 API:分页、过滤与跨租户 404(零网络)。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT


def _execute(
    client: TestClient,
    tool_call_id: str,
    *,
    run_id: str | None = None,
    path: str = "a.txt",
) -> Any:
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "arguments": {"path": path},
    }
    if run_id is not None:
        payload["run_id"] = run_id
    return client.post("/v1/tools/read:execute", json=payload, headers=TENANT)


def test_list_is_empty_before_any_call(client: TestClient) -> None:
    resp = client.get("/v1/tool-calls", headers=TENANT)

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_list_returns_audit_rows(client: TestClient) -> None:
    _execute(client, "call-1", run_id="run-1")

    resp = client.get("/v1/tool-calls", headers=TENANT)

    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["tool_call_id"] == "call-1"
    assert item["tool_name"] == "read"
    assert item["risk"] == "read"
    assert item["decision"] == "auto"
    assert item["ok"] is True
    assert item["error_code"] is None
    assert item["paths"] == ["a.txt"]
    assert item["sandbox_id"] == "sbx-1"
    assert item["run_id"] == "run-1"


def test_list_filters_by_run_id(client: TestClient) -> None:
    _execute(client, "call-1", run_id="run-1")
    _execute(client, "call-2", run_id="run-2")

    resp = client.get("/v1/tool-calls", params={"run_id": "run-2"}, headers=TENANT)

    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["tool_call_id"] == "call-2"


def test_list_filters_by_tool_name_and_ok(
    client: TestClient, sandbox_provider: Any
) -> None:
    _execute(client, "call-1", run_id="run-1")
    sandbox_provider.exit_code = 1
    sandbox_provider.stderr = "boom"
    _execute(client, "call-2", run_id="run-1")

    failed = client.get("/v1/tool-calls", params={"ok": "false"}, headers=TENANT)
    assert failed.json()["total"] == 1
    assert failed.json()["items"][0]["tool_call_id"] == "call-2"

    other_tool = client.get(
        "/v1/tool-calls", params={"tool_name": "grep"}, headers=TENANT
    )
    assert other_tool.json()["total"] == 0


def test_list_paginates(client: TestClient) -> None:
    _execute(client, "call-1")
    _execute(client, "call-2")

    page = client.get(
        "/v1/tool-calls", params={"limit": 1, "offset": 0}, headers=TENANT
    )
    rest = client.get(
        "/v1/tool-calls", params={"limit": 1, "offset": 1}, headers=TENANT
    )

    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1
    assert len(rest.json()["items"]) == 1
    assert page.json()["items"][0]["id"] != rest.json()["items"][0]["id"]


def test_list_rejects_invalid_pagination(client: TestClient) -> None:
    assert (
        client.get("/v1/tool-calls", params={"limit": 0}, headers=TENANT).status_code
        == 422
    )
    assert (
        client.get("/v1/tool-calls", params={"offset": -1}, headers=TENANT).status_code
        == 422
    )


def test_get_detail_by_tool_call_id(client: TestClient) -> None:
    _execute(client, "call-1", run_id="run-1")

    resp = client.get("/v1/tool-calls/call-1", headers=TENANT)

    assert resp.status_code == 200
    assert resp.json()["tool_call_id"] == "call-1"
    assert resp.json()["run_id"] == "run-1"


def test_get_detail_unknown_is_404(client: TestClient) -> None:
    resp = client.get("/v1/tool-calls/missing", headers=TENANT)

    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.call_not_found"


def test_get_detail_cross_tenant_is_404(client: TestClient) -> None:
    _execute(client, "call-1")

    resp = client.get("/v1/tool-calls/call-1", headers=OTHER)

    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.call_not_found"


def test_list_requires_bearer_token(client: TestClient) -> None:
    resp = client.get("/v1/tool-calls")

    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.token_missing"
