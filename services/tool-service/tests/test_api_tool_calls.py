"""审计查询 API:分页、过滤与跨租户 404(零网络)。"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeSandboxProvider


def _execute(
    client: TestClient,
    tool_call_id: str,
    *,
    run_id: str | None = None,
    conversation_id: str | None = None,
    path: str = "a.txt",
) -> Any:
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "arguments": {"path": path},
    }
    if run_id is not None:
        payload["run_id"] = run_id
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
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


def test_list_filters_by_conversation_id(client: TestClient) -> None:
    _execute(client, "call-1", conversation_id="c-1")
    _execute(client, "call-2", conversation_id="c-2")

    resp = client.get(
        "/v1/tool-calls", params={"conversation_id": "c-2"}, headers=TENANT
    )

    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["tool_call_id"] == "call-2"
    assert body["items"][0]["conversation_id"] == "c-2"


_PATCH = "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n hello\n-world\n+there\n"


def _write_with_patch(
    client: TestClient,
    sandbox_provider: FakeSandboxProvider,
    tool_call_id: str,
    *,
    conversation_id: str | None = None,
) -> Any:
    """先 read 满足读前置,再让沙箱返回带补丁的 write 信封。"""

    _execute(client, f"{tool_call_id}-read", conversation_id=conversation_id)
    sandbox_provider.stdout = json.dumps(
        {
            "ok": True,
            "path": "a.txt",
            "created": False,
            "changed": True,
            "bytes": 12,
            "lines": 2,
            "sha256_before": "0" * 64,
            "sha256_after": "1" * 64,
            "notes": [],
            "patch": _PATCH,
            "lines_added": 1,
            "lines_removed": 1,
            "patch_truncated": False,
        }
    )
    payload: dict[str, Any] = {
        "tool_call_id": tool_call_id,
        "arguments": {"path": "a.txt", "content": "hello\nthere\n"},
        "approved": True,
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return client.post("/v1/tools/write:execute", json=payload, headers=TENANT)


def test_include_patch_controls_patch_payload(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    write = _write_with_patch(client, sandbox_provider, "call-w", conversation_id="c-1")

    assert write.status_code == 200
    # 回给编排器/模型的工具结果不含补丁(补丁只落审计库)。
    assert "patch" not in write.json()
    assert "lines_added" not in write.json()

    plain = client.get(
        "/v1/tool-calls", params={"tool_name": "write"}, headers=TENANT
    ).json()["items"][0]
    assert plain["patch"] is None
    assert plain["lines_added"] == 1
    assert plain["lines_removed"] == 1
    assert plain["patch_truncated"] is False

    listed = client.get(
        "/v1/tool-calls",
        params={"tool_name": "write", "include_patch": "true"},
        headers=TENANT,
    ).json()["items"][0]
    assert listed["patch"] == _PATCH

    detail = client.get(
        "/v1/tool-calls/call-w", params={"include_patch": "true"}, headers=TENANT
    )
    assert detail.json()["patch"] == _PATCH

    detail_plain = client.get("/v1/tool-calls/call-w", headers=TENANT)
    assert detail_plain.json()["patch"] is None
