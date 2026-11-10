"""工具目录 API:清单、按 kind/scope 过滤、按名/别名取详情与 404。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    ACCOUNT,
)
from tool_service.services import catalog


def _execute(
    client: TestClient,
    payload: dict[str, Any],
    *,
    tool: str = "read",
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        f"/v1/tools/{tool}:execute", json=payload, headers=headers or ACCOUNT
    )


def test_list_tools_exposes_function_slice(client: TestClient) -> None:
    resp = client.get("/v1/tools")

    assert resp.status_code == 200
    body = resp.json()
    assert body["catalog_version"]
    assert [tool["name"] for tool in body["tools"]] == [
        "read",
        "glob",
        "grep",
        "list_dir",
        "write",
        "edit",
        "search_knowledge",
        "ask_question",
    ]
    assert body["tools"][0]["executor"] == "sandbox"
    assert body["tools"][0]["risk"] == "read"
    assert body["tools"][4]["risk"] == "write"
    assert body["tools"][6]["executor"] == "http"
    assert body["tools"][6]["risk"] == "read"
    assert body["tools"][7]["kind"] == "interaction"
    assert body["tools"][7]["executor"] == "none"


def test_list_tools_filters_by_kind_and_scope(client: TestClient) -> None:
    interaction = client.get("/v1/tools", params={"kind": "interaction"})
    assert interaction.status_code == 200
    assert [tool["name"] for tool in interaction.json()["tools"]] == ["ask_question"]

    function = client.get("/v1/tools", params={"kind": "function"})
    assert function.status_code == 200
    function_names = [tool["name"] for tool in function.json()["tools"]]
    assert "ask_question" not in function_names
    assert "search_knowledge" in function_names

    unknown_scope = client.get("/v1/tools", params={"scope": "missing"})
    assert unknown_scope.status_code == 200
    assert unknown_scope.json()["tools"] == []


def test_get_tool_by_name_and_alias(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    by_name = client.get("/v1/tools/read")
    assert by_name.status_code == 200
    assert by_name.json()["name"] == "read"
    assert by_name.json()["input_schema"]["properties"]["path"]["type"] == "string"

    # 目录不再自带旧别名;别名解析仍是通用机制(临时注册验证一次)。
    monkeypatch.setitem(catalog.ALIASES, "legacy_local_read", "read")
    by_alias = client.get("/v1/tools/legacy_local_read")
    assert by_alias.status_code == 200
    assert by_alias.json()["name"] == "read"


def test_get_unknown_tool_is_404_but_interaction_is_listed(client: TestClient) -> None:
    missing = client.get("/v1/tools/nope")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tool.not_found"

    interaction = client.get("/v1/tools/ask_question")
    assert interaction.status_code == 200
    assert interaction.json()["name"] == "ask_question"
    assert interaction.json()["kind"] == "interaction"
    assert interaction.json()["executor"] == "none"


def test_job_tools_are_hidden_when_jobs_are_disabled(client: TestClient) -> None:
    names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
    for job_tool in ("job_start", "job_output", "job_kill"):
        assert job_tool not in names
        detail = client.get(f"/v1/tools/{job_tool}")
        assert detail.status_code == 404
        assert detail.json()["code"] == "tool.not_found"
        assert job_tool in detail.json()["message"]

    resp = client.post(
        "/v1/tools/job_start:execute",
        json={"tool_call_id": "call-job", "arguments": {"command": "echo hi"}},
        headers=ACCOUNT,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"
