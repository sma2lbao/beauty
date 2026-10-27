"""目录与执行 API:别名机制、幂等、错误信封与租户边界(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from common.service_testkit import app_client
from fastapi.testclient import TestClient

from tests.conftest import (
    OTHER,
    TENANT,
    FakeKnowledgeClient,
    FakeSandboxProvider,
    _settings,
    read_envelope,
    verifier,
    write_envelope,
)
from tool_service.main import create_app
from tool_service.services import catalog
from tool_service.services.executors.fetch import FetchClient
from tool_service.services.executors.http import HttpToolExecutor


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


def test_execute_ask_question_returns_interaction_envelope(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    arguments = {
        "questions": [
            {
                "question": "选哪个环境?",
                "header": "环境",
                "options": [
                    {"label": "staging", "description": "预发"},
                    {"label": "prod"},
                ],
            }
        ],
        "allow_custom": False,
    }

    resp = _execute(
        client,
        {"tool_call_id": "call-3", "arguments": arguments},
        tool="ask_question",
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["kind"] == "interaction"
    assert body["interaction"] == {"type": "question"}
    assert body["normalized"]["allow_custom"] is False
    assert body["normalized"]["questions"][0]["header"] == "环境"
    assert body["normalized"]["questions"][0]["multi_select"] is False
    # 交互工具只归一参数,不触发沙箱执行。
    assert sandbox_provider.exec_calls == []


def test_execute_ask_question_invalid_arguments_is_200_error(
    client: TestClient, sandbox_provider: FakeSandboxProvider
) -> None:
    resp = _execute(
        client,
        {"tool_call_id": "call-3b", "arguments": {"questions": []}},
        tool="ask_question",
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["error"]["code"] == "tool.invalid_args"
    assert sandbox_provider.exec_calls == []


def test_execute_search_knowledge_proxies_to_knowledge(
    client: TestClient, knowledge_client: Any
) -> None:
    knowledge_client.result = {
        "query": "部署",
        "provider": "fake",
        "model": "fake",
        "hits": [{"title": "发布流程", "score": 0.8, "content": "先灰度"}],
    }

    resp = _execute(
        client,
        {"tool_call_id": "call-kb", "arguments": {"query": "部署"}},
        tool="search_knowledge",
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["hits"] == [{"title": "发布流程", "score": 0.8, "content": "先灰度"}]
    assert body["tool_call_id"] == "call-kb"
    assert knowledge_client.calls == [{"tenant_id": "t-1", "query": "部署", "limit": 5}]


def test_execute_search_knowledge_upstream_error_is_envelope(
    client: TestClient, knowledge_client: Any
) -> None:
    from tool_service.services.executors.http import UpstreamStatusError

    knowledge_client.error = UpstreamStatusError(503, "down")

    resp = _execute(
        client,
        {"tool_call_id": "call-kb2", "arguments": {"query": "部署"}},
        tool="search_knowledge",
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "tool.upstream_unavailable"
    assert body["error"]["retryable"] is True


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


def _custom_client(
    tmp_path: Path, provider: FakeSandboxProvider, **overrides: Any
) -> TestClient:
    app = create_app(
        _settings(tmp_path, **overrides),
        verifier(),
        sandbox_provider=provider,
    )
    return TestClient(app)


def test_total_call_budget_is_enforced(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    with _custom_client(tmp_path, provider, max_tool_calls_per_run=0) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-budget",
                "arguments": {"path": "a.txt"},
                "run_id": "run-budget",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == "tool.budget_exceeded"
    assert provider.exec_calls == []


def test_write_quota_is_enforced(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = read_envelope()
    with _custom_client(tmp_path, provider, max_writes_per_run=0) as client:
        resp = _execute(
            client,
            {
                "tool_call_id": "call-quota",
                "arguments": {"path": "a.txt", "content": "x"},
                "approved": True,
                "run_id": "run-quota",
            },
            tool="write",
        )

    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == "tool.budget_exceeded"
    assert provider.exec_calls == []


def _mock_fetcher(handler: Any) -> FetchClient:
    """注入 MockTransport 与固定公网解析结果,零网络。"""

    async def resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    return FetchClient(transport=httpx.MockTransport(handler), resolver=resolver)


def test_fetch_is_hidden_until_enabled(client: TestClient) -> None:
    """能力即权限边界:未开 fetch 时目录不暴露,执行也明确报不可用。"""

    names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
    assert "fetch" not in names

    missing = client.get("/v1/tools/fetch")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tool.not_found"

    resp = _execute(
        client,
        {
            "tool_call_id": "call-fetch-off",
            "arguments": {"url": "https://example.com/"},
        },
        tool="fetch",
    )
    # 执行入口与目录一致 404:关闭的工具不是“存在但不可用”,而是不存在。
    assert resp.status_code == 404
    assert resp.json()["code"] == "tool.not_found"


def test_fetch_is_listed_and_executed_when_enabled(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"# ai-infra-book", headers={"content-type": "text/plain"}
        )

    fetcher = _mock_fetcher(handler)
    app = create_app(
        _settings(tmp_path, fetch_enabled=True),
        verifier(),
        sandbox_provider=FakeSandboxProvider(),
        http_executor=HttpToolExecutor(
            knowledge=FakeKnowledgeClient(), fetcher=fetcher
        ),
    )
    with app_client(app) as client:
        names = [tool["name"] for tool in client.get("/v1/tools").json()["tools"]]
        assert "fetch" in names

        resp = _execute(
            client,
            {
                "tool_call_id": "call-fetch-on",
                "arguments": {"url": "https://example.com/readme"},
            },
            tool="fetch",
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["text"] == "# ai-infra-book"
    assert body["final_url"] == "https://example.com/readme"
    assert body["status"] == 200
