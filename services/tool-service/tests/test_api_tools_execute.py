"""执行 API:鉴权、幂等/冲突/重放、信封、请求形状与审计归属边界。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import (
    OTHER,
    TENANT,
    FakeSandboxProvider,
)


def _execute(
    client: TestClient,
    payload: dict[str, Any],
    *,
    tool: str = "read",
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"/v1/tools/{tool}:execute", json=payload, headers=headers or TENANT
    )


def test_execute_requires_bearer_token(client: TestClient) -> None:
    resp = client.post(
        "/v1/tools/read:execute",
        json={"tool_call_id": "call-1", "arguments": {"path": "a.txt"}},
    )

    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.token_missing"


def test_execute_read_returns_flattened_envelope(client: TestClient) -> None:
    resp = _execute(
        client,
        {"tool_call_id": "call-1", "arguments": {"path": "a.txt"}},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["path"] == "a.txt"
    assert body["tool_call_id"] == "call-1"
    assert body["idempotent_replay"] is False
    assert body["truncated"] is False
    assert "duration_ms" in body


def test_execute_is_idempotent_for_same_arguments(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    payload = {"tool_call_id": "call-1", "arguments": {"path": "a.txt"}}

    first = _execute(client, payload)
    second = _execute(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["idempotent_replay"] is True
    assert body["replayed"] is True
    assert len(sandbox_provider.exec_calls) == 1


def test_execute_conflicting_arguments_is_409(client: TestClient) -> None:
    _execute(client, {"tool_call_id": "call-1", "arguments": {"path": "a.txt"}})

    resp = _execute(client, {"tool_call_id": "call-1", "arguments": {"path": "b.txt"}})

    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "tool.idempotency_conflict"
    assert body["details"]["tool_call_id"] == "call-1"


def test_execute_failed_envelope_is_replayed(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    sandbox_provider.exit_code = 1
    sandbox_provider.stderr = "boom"
    payload = {"tool_call_id": "call-1", "arguments": {"path": "a.txt"}}

    first = _execute(client, payload)
    second = _execute(client, payload)

    assert first.status_code == 200
    assert first.json()["ok"] is False
    assert first.json()["error"]["code"] == "tool.io_error"
    assert second.json()["idempotent_replay"] is True
    assert second.json()["error"]["code"] == "tool.io_error"
    assert len(sandbox_provider.exec_calls) == 1


def test_invalid_arguments_return_200_error_envelope(client: TestClient) -> None:
    resp = _execute(client, {"tool_call_id": "call-2", "arguments": {}})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.invalid_args"

    audit = client.get("/v1/tool-calls", params={"tool_name": "read"}, headers=TENANT)
    assert audit.json()["items"][0]["ok"] is False
    assert audit.json()["items"][0]["error_code"] == "tool.invalid_args"


def test_execute_rejects_bad_request_shape(client: TestClient) -> None:
    missing_id = _execute(client, {"arguments": {"path": "a.txt"}})
    assert missing_id.status_code == 422

    extra_field = _execute(
        client,
        {"tool_call_id": "call-4", "arguments": {"path": "a.txt"}, "extra": 1},
    )
    assert extra_field.status_code == 422

    bad_timeout = _execute(
        client,
        {
            "tool_call_id": "call-5",
            "arguments": {"path": "a.txt"},
            "timeout_seconds": 0,
        },
    )
    assert bad_timeout.status_code == 422


def test_execute_records_approved_decision(client: TestClient) -> None:
    resp = _execute(
        client,
        {
            "tool_call_id": "call-6",
            "arguments": {"path": "a.txt"},
            "approved": True,
            "run_id": "run-1",
        },
    )

    assert resp.status_code == 200
    audit = client.get("/v1/tool-calls", headers=TENANT)
    item = audit.json()["items"][0]
    assert item["decision"] == "approved"
    assert item["run_id"] == "run-1"
    assert item["sandbox_id"] == "sbx-1"


def test_execute_uses_conversation_scope(client: TestClient) -> None:
    resp = _execute(
        client,
        {
            "tool_call_id": "call-7",
            "arguments": {"path": "a.txt"},
            "conversation_id": "c-1",
        },
    )

    assert resp.status_code == 200


def test_tool_calls_are_tenant_scoped(client: TestClient) -> None:
    _execute(client, {"tool_call_id": "call-1", "arguments": {"path": "a.txt"}})

    other = client.get("/v1/tool-calls", headers=OTHER)
    assert other.status_code == 200
    assert other.json()["total"] == 0
