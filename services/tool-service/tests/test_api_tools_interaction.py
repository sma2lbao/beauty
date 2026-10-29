"""交互与外部只读工具 API:ask_question 信封、search_knowledge 代理与上游错误。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import (
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
