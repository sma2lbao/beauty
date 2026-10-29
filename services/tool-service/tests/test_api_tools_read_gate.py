"""写工具读门禁:确认、新建免读、conversation scope 与 expected_sha 链路。"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import (
    TENANT,
    FakeSandboxProvider,
    write_envelope,
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


def test_write_requires_confirmation(client: TestClient) -> None:
    resp = _execute(
        client,
        {"tool_call_id": "call-8", "arguments": {"path": "a.txt", "content": "x"}},
        tool="write",
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == "tool.confirmation_required"


def test_write_new_file_without_read_is_allowed(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """新建文件(目标不存在)无需先 read:放行并附内部「可能不存在」标记。"""

    sandbox_provider.stdout = write_envelope("new.txt", sha_before=None)
    resp = _execute(
        client,
        {
            "tool_call_id": "call-9",
            "arguments": {"path": "new.txt", "content": "x"},
            "approved": True,
            "run_id": "run-write",
        },
        tool="write",
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(sandbox_provider.exec_calls) == 1
    request = json.loads(sandbox_provider.exec_calls[0]["stdin"])
    assert request["arguments"]["_read_gate_absent"] is True
    assert "_expected_read_sha256" not in request["arguments"]
    audit = client.get("/v1/tool-calls", params={"tool_name": "write"}, headers=TENANT)
    assert audit.json()["items"][0]["ok"] is True


def test_write_new_file_in_conversation_scope_is_allowed(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """会话作用域(无 run_id)同样支持新建文件免读。"""

    sandbox_provider.stdout = write_envelope("book.md", sha_before=None)
    resp = _execute(
        client,
        {
            "tool_call_id": "call-9b",
            "arguments": {"path": "book.md", "content": "# 书"},
            "approved": True,
            "conversation_id": "c-1",
        },
        tool="write",
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    request = json.loads(sandbox_provider.exec_calls[0]["stdin"])
    assert request["arguments"]["_read_gate_absent"] is True


def test_edit_without_read_is_rejected_with_envelope(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    """edit 必须有读记录:改已有内容不能靠「新建」放行。"""

    resp = _execute(
        client,
        {
            "tool_call_id": "call-9c",
            "arguments": {"path": "a.txt", "old_string": "hello", "new_string": "hi"},
            "approved": True,
            "run_id": "run-write",
        },
        tool="edit",
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.read_required"
    assert sandbox_provider.exec_calls == []
    audit = client.get("/v1/tool-calls", params={"tool_name": "edit"}, headers=TENANT)
    assert audit.json()["items"][0]["error_code"] == "tool.read_required"


def test_read_then_write_passes_expected_sha(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    read = _execute(
        client,
        {
            "tool_call_id": "call-10",
            "arguments": {"path": "a.txt"},
            "run_id": "run-rw",
        },
    )
    assert read.status_code == 200
    assert read.json()["ok"] is True

    sandbox_provider.stdout = write_envelope("a.txt", sha_before="0" * 64)
    write = _execute(
        client,
        {
            "tool_call_id": "call-11",
            "arguments": {"path": "a.txt", "content": "hello"},
            "approved": True,
            "run_id": "run-rw",
        },
        tool="write",
    )

    assert write.status_code == 200
    body = write.json()
    assert body["ok"] is True
    assert body["changed"] is True
    assert len(sandbox_provider.exec_calls) == 2
    request = json.loads(sandbox_provider.exec_calls[1]["stdin"])
    assert request["arguments"]["_expected_read_sha256"] == "0" * 64
    audit = client.get("/v1/tool-calls", params={"tool_name": "write"}, headers=TENANT)
    item = audit.json()["items"][0]
    assert item["ok"] is True
    assert item["bytes_written"] == 12
    assert item["sha256_after"] == "1" * 64
