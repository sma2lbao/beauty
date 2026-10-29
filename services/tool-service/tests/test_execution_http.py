"""search_knowledge:HTTP 执行器代理到知识库服务(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from common.errors import UpstreamError

from tests.conftest import FakeKnowledgeClient, FakeSandboxProvider
from tests.execution_support import execution_context, make_runner
from tool_service.services.execution import (
    ToolRunner,
    audit_columns,
)
from tool_service.services.executors.http import HttpToolExecutor, UpstreamStatusError

# ---- search_knowledge:HTTP 执行器代理到知识库(零网络) -------------------


def make_http_runner(
    tmp_path: Path,
    provider: FakeSandboxProvider,
    knowledge: FakeKnowledgeClient,
) -> ToolRunner:
    return make_runner(tmp_path, provider, http=HttpToolExecutor(knowledge=knowledge))


async def test_search_knowledge_proxies_to_knowledge_client(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    knowledge = FakeKnowledgeClient()
    knowledge.result = {
        "query": "部署",
        "provider": "fake",
        "model": "fake",
        "hits": [{"title": "T", "score": 0.9, "content": "C"}],
    }
    runner = make_http_runner(tmp_path, provider, knowledge)
    context = execution_context()

    outcome = await runner.execute(context, "search_knowledge", {"query": "部署"})

    assert outcome.response.ok is True
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.result == knowledge.result
    assert outcome.sandbox_id is None
    assert knowledge.calls == [{"tenant_id": "t-1", "query": "部署", "limit": 5}]
    assert provider.ensured == []
    assert provider.exec_calls == []
    columns = audit_columns(outcome, context, {"query": "部署"}, decision="auto")
    assert columns["tool_name"] == "search_knowledge"
    assert columns["risk"] == "read"
    assert columns["bytes_written"] == 0
    assert json.loads(columns["paths"]) == []
    assert columns["result_chars"] > 0


async def test_search_knowledge_forwards_limit(tmp_path: Path) -> None:
    knowledge = FakeKnowledgeClient()
    runner = make_http_runner(tmp_path, FakeSandboxProvider(), knowledge)

    await runner.execute(
        execution_context(), "search_knowledge", {"query": "q", "limit": 9}
    )

    assert knowledge.calls[0]["limit"] == 9


async def test_search_knowledge_requires_http_executor(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(
        execution_context(), "search_knowledge", {"query": "q"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.executor_unavailable"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (UpstreamStatusError(404, "missing"), "tool.not_found", False),
        (UpstreamStatusError(422, "bad"), "tool.invalid_args", False),
        (UpstreamStatusError(503, "down"), "tool.upstream_unavailable", True),
        (httpx.ReadTimeout("slow"), "tool.timeout", True),
        (httpx.ConnectError("refused"), "tool.upstream_unavailable", True),
        (
            UpstreamError("auth.internal_secret_missing", "no secret"),
            "tool.upstream_unavailable",
            False,
        ),
        (
            UpstreamError("knowledge.bad_response", "bad json"),
            "tool.upstream_unavailable",
            True,
        ),
    ],
)
async def test_search_knowledge_maps_upstream_errors(
    tmp_path: Path, error: Exception, code: str, retryable: bool
) -> None:
    knowledge = FakeKnowledgeClient()
    knowledge.error = error
    runner = make_http_runner(tmp_path, FakeSandboxProvider(), knowledge)

    outcome = await runner.execute(
        execution_context(), "search_knowledge", {"query": "q"}
    )

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == code
    assert outcome.response.error.retryable is retryable
