"""执行编排:信封、风险下限、超时、幂等摘要与审计映射(零网络)。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from common.clients.sandbox import SandboxExecResult
from common.errors import NotFoundError, PermissionDeniedError, UpstreamError
from common.tools import ExecutorKind, ToolRisk

from tests.conftest import FakeKnowledgeClient, FakeSandboxProvider, _settings
from tool_service.constants import MAX_TOOL_CHARS
from tool_service.services import catalog, execution
from tool_service.services.execution import (
    ExecutionContext,
    ToolRunner,
    arguments_digest,
    audit_columns,
)
from tool_service.services.executors.http import HttpToolExecutor, UpstreamStatusError


def _context(**overrides: Any) -> ExecutionContext:
    values: dict[str, Any] = {
        "tenant_id": "t-1",
        "tool_call_id": "call-1",
        "conversation_id": "c-1",
    }
    values.update(overrides)
    return ExecutionContext(**values)


def _runner(
    tmp_path: Path,
    provider: FakeSandboxProvider,
    *,
    http: HttpToolExecutor | None = None,
) -> ToolRunner:
    return ToolRunner(settings=_settings(tmp_path), sandbox=provider, http=http)


async def test_execute_read_returns_envelope(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is True
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.idempotent_replay is False
    assert outcome.sandbox_id == "sbx-1"
    assert outcome.response.duration_ms == 5
    assert provider.ensured[0]["scope_key"] == "conversation:c-1"
    assert provider.exec_calls[0]["argv"][0] == "python3"
    request = json.loads(provider.exec_calls[0]["stdin"])
    assert request["tool"] == "read"
    assert request["arguments"] == {"path": "a.txt"}
    assert "policy" in request
    assert provider.touched == ["sbx-1"]


async def test_execute_resolves_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)
    monkeypatch.setitem(catalog.ALIASES, "legacy_local_read", "read")

    outcome = await runner.execute(_context(), "legacy_local_read", {"path": "a.txt"})

    assert outcome.descriptor.name == "read"
    assert json.loads(provider.exec_calls[0]["stdin"])["tool"] == "read"


async def test_invalid_arguments_return_error_envelope(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert provider.exec_calls == []


async def test_unknown_tool_raises_not_found(tmp_path: Path) -> None:
    runner = _runner(tmp_path, FakeSandboxProvider())

    with pytest.raises(NotFoundError) as raised:
        await runner.execute(_context(), "nope", {})

    assert raised.value.code == "tool.not_found"


async def test_disabled_tool_raises_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, enabled=False)
    monkeypatch.setattr(execution, "find_descriptor", lambda name: descriptor)
    runner = _runner(tmp_path, FakeSandboxProvider())

    with pytest.raises(NotFoundError) as raised:
        await runner.execute(_context(), "read", {"path": "a.txt"})

    assert raised.value.code == "tool.not_found"


async def test_write_requires_approval(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    with pytest.raises(PermissionDeniedError) as raised:
        await runner.execute(_context(), "write", {"path": "a.txt", "content": "x"})

    assert raised.value.code == "tool.confirmation_required"
    assert provider.exec_calls == []


async def test_write_with_approval_merges_internal_arguments(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps(
        {
            "ok": True,
            "path": "a.txt",
            "created": False,
            "changed": True,
            "bytes": 7,
            "lines": 1,
            "sha256_before": "0" * 64,
            "sha256_after": "1" * 64,
            "notes": [],
        }
    )
    runner = _runner(tmp_path, provider)
    context = _context(approved=True)
    arguments = {"path": "a.txt", "content": "hello"}

    outcome = await runner.execute(
        context,
        "write",
        arguments,
        internal_arguments={"_expected_read_sha256": "0" * 64},
    )
    request = json.loads(provider.exec_calls[0]["stdin"])
    columns = audit_columns(outcome, context, arguments, decision="approved")

    assert outcome.response.ok is True
    assert request["arguments"]["_expected_read_sha256"] == "0" * 64
    # 审计只记录调用方参数,内部参数不落库
    assert json.loads(columns["arguments"]) == arguments
    assert columns["bytes_written"] == 7


# ---- 服务端补丁:只落审计,不进模型可见结果 -----------------------------

_PATCH = "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-hello\n+hi\n"


def _write_patch_envelope(*, patch: str = _PATCH) -> str:
    return json.dumps(
        {
            "ok": True,
            "path": "a.txt",
            "created": False,
            "changed": True,
            "bytes": 3,
            "lines": 1,
            "sha256_before": "0" * 64,
            "sha256_after": "1" * 64,
            "notes": [],
            "patch": patch,
            "lines_added": 1,
            "lines_removed": 1,
            "patch_truncated": False,
        }
    )


async def test_write_patch_is_stripped_from_model_visible_result(
    tmp_path: Path,
) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = _write_patch_envelope()
    runner = _runner(tmp_path, provider)
    context = _context(approved=True)
    arguments = {"path": "a.txt", "content": "hi"}

    outcome = await runner.execute(context, "write", arguments)
    columns = audit_columns(outcome, context, arguments, decision="approved")

    result = outcome.response.result or {}
    assert "patch" not in result
    assert "lines_added" not in result
    assert "lines_removed" not in result
    assert "patch_truncated" not in result
    assert "patch" not in outcome.response.envelope()
    assert outcome.patch is not None
    assert outcome.patch.patch == _PATCH
    assert columns["patch"] == _PATCH
    assert columns["lines_added"] == 1
    assert columns["lines_removed"] == 1
    assert columns["patch_truncated"] is False


async def test_large_patch_does_not_trip_result_size_limit(tmp_path: Path) -> None:
    """补丁可能上万字符:剥离后不能触发 MAX_TOOL_CHARS,也不能回给模型。"""

    huge_patch = "x" * (MAX_TOOL_CHARS + 100)
    provider = FakeSandboxProvider()
    provider.stdout = _write_patch_envelope(patch=huge_patch)
    runner = _runner(tmp_path, provider)
    context = _context(approved=True)

    outcome = await runner.execute(context, "write", {"path": "a.txt", "content": "hi"})

    assert outcome.response.ok is True
    assert outcome.patch is not None
    assert outcome.patch.patch == huge_patch
    assert "patch" not in (outcome.response.result or {})


async def test_read_result_has_no_patch_audit(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)
    context = _context()

    outcome = await runner.execute(context, "read", {"path": "a.txt"})
    columns = audit_columns(outcome, context, {"path": "a.txt"}, decision="auto")

    assert outcome.patch is None
    assert columns["patch"] is None
    assert columns["lines_added"] is None
    assert columns["lines_removed"] is None
    assert columns["patch_truncated"] is False


async def test_destructive_requires_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, risk=ToolRisk.DESTRUCTIVE)
    monkeypatch.setattr(execution, "find_descriptor", lambda name: descriptor)
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    with pytest.raises(PermissionDeniedError) as raised:
        await runner.execute(_context(), "read", {"path": "a.txt"})

    assert raised.value.code == "tool.confirmation_required"
    assert provider.exec_calls == []


async def test_destructive_with_approval_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, risk=ToolRisk.DESTRUCTIVE)
    monkeypatch.setattr(execution, "find_descriptor", lambda name: descriptor)
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(approved=True), "read", {"path": "a.txt"})

    assert outcome.response.ok is True


async def test_http_executor_is_not_wired_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, executor=ExecutorKind.HTTP)
    monkeypatch.setattr(execution, "find_descriptor", lambda name: descriptor)
    runner = _runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.executor_unavailable"


async def test_none_executor_is_not_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    descriptor = replace(catalog.READ, executor=ExecutorKind.NONE)
    monkeypatch.setattr(execution, "find_descriptor", lambda name: descriptor)
    runner = _runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.not_supported"


async def test_helper_exit_code_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exit_code = 1
    provider.stdout = ""
    provider.stderr = "traceback: boom"
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"
    assert "boom" in outcome.response.error.hint
    assert outcome.response.error.retryable is True


async def test_helper_bad_json_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "not json"
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"


async def test_helper_non_envelope_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"tool": "read"})
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"
    assert "信封" in outcome.response.error.message


async def test_helper_unparsable_envelope_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"ok": True, "duration_ms": "abc"})
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"
    assert "无法解析" in outcome.response.error.message


async def test_helper_error_envelope_is_preserved(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps(
        {
            "ok": False,
            "error": {
                "code": "tool.not_found",
                "message": "文件不存在",
                "hint": "先 glob 确认路径",
                "retryable": False,
            },
        }
    )
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.not_found"


async def test_oversized_result_becomes_too_large(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"ok": True, "content": "x" * 30000})
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.too_large"


async def test_result_under_limit_is_returned(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"ok": True, "content": "x" * 100})
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is True
    assert outcome.response.result == {"content": "x" * 100}


class SlowExecProvider(FakeSandboxProvider):
    """ensure 立即返回、exec 挂住,用于执行超时分支。"""

    async def exec(
        self,
        tenant_id: str,
        sandbox_id: str,
        argv: list[str],
        *,
        cwd: str = "/workspace",
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxExecResult:
        await asyncio.sleep(5)
        raise AssertionError("超时路径不应执行到这里")


def _shrink_timeout(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.3) -> None:
    monkeypatch.setattr(
        execution.ToolRunner,
        "_timeout_seconds",
        lambda self, context, descriptor: seconds,
    )
    monkeypatch.setattr(execution, "SANDBOX_CALL_GRACE_SECONDS", 0.01)


async def test_exec_timeout_returns_retryable_error_and_touches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shrink_timeout(monkeypatch)
    provider = SlowExecProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert outcome.response.error.retryable is True
    assert provider.touched == ["sbx-1"]


class SlowEnsureProvider(FakeSandboxProvider):
    async def ensure(
        self, tenant_id: str, *, scope_key: str, run_id: str | None = None
    ) -> str:
        await asyncio.sleep(5)
        raise AssertionError("超时路径不应执行到这里")


async def test_ensure_timeout_returns_retryable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shrink_timeout(monkeypatch)
    runner = _runner(tmp_path, SlowEnsureProvider())

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.timeout"
    assert "沙箱" in outcome.response.error.message


async def test_touch_failure_does_not_fail_call(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.touch_error = UpstreamError("sandbox.unreachable", "down")
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is True


async def test_ensure_upstream_error_propagates(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.ensure_error = UpstreamError("sandbox.unreachable", "down")
    runner = _runner(tmp_path, provider)

    with pytest.raises(UpstreamError):
        await runner.execute(_context(), "read", {"path": "a.txt"})


async def test_exec_upstream_error_propagates_and_touches(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exec_error = UpstreamError("sandbox.upstream_error", "boom")
    runner = _runner(tmp_path, provider)

    with pytest.raises(UpstreamError):
        await runner.execute(_context(), "read", {"path": "a.txt"})

    assert provider.touched == ["sbx-1"]


async def test_request_timeout_override_is_forwarded(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    await runner.execute(_context(timeout_seconds=2), "read", {"path": "a.txt"})

    assert provider.exec_calls[0]["timeout_seconds"] == 2


async def test_workspace_scope_uses_explicit_scope_key(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    await runner.execute(
        _context(conversation_id=None, run_id="r-9"), "read", {"path": "a.txt"}
    )

    assert provider.ensured[0]["scope_key"] == "run:r-9"
    assert provider.ensured[0]["run_id"] == "r-9"


def test_arguments_digest_ignores_key_order() -> None:
    assert arguments_digest({"b": 2, "a": 1}) == arguments_digest({"a": 1, "b": 2})
    assert arguments_digest({"a": 1}) != arguments_digest({"a": 2})


async def test_audit_columns_for_success(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)
    context = _context(conversation_id="c-1")
    arguments = {"path": "a.txt"}

    outcome = await runner.execute(context, "read", arguments)
    columns = audit_columns(outcome, context, arguments, decision="auto")

    assert columns["tool_name"] == "read"
    assert columns["tool_version"] == outcome.descriptor.version
    assert columns["risk"] == "read"
    assert columns["decision"] == "auto"
    assert columns["ok"] is True
    assert columns["error_code"] is None
    assert columns["duration_ms"] == 5
    assert columns["result_chars"] > 0
    assert columns["bytes_written"] == 0
    assert columns["truncated"] is False
    assert json.loads(columns["paths"]) == ["a.txt"]
    assert columns["sha256_before"] is None
    assert columns["sha256_after"] is None
    assert columns["sandbox_id"] == "sbx-1"
    assert columns["arguments_sha256"] == arguments_digest(arguments)
    assert json.loads(columns["arguments"]) == arguments


async def test_audit_columns_for_failure(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)
    context = _context()

    outcome = await runner.execute(context, "read", {})
    columns = audit_columns(outcome, context, {}, decision="auto")

    assert columns["ok"] is False
    assert columns["error_code"] == "tool.invalid_args"
    assert columns["result_chars"] == 0
    assert json.loads(columns["paths"]) == []
    assert columns["sandbox_id"] is None


async def test_audit_paths_are_capped(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    matches = [{"path": f"file-{index}.py", "line": 1} for index in range(30)]
    provider.stdout = json.dumps({"ok": True, "matches": matches})
    runner = _runner(tmp_path, provider)
    context = _context()
    arguments = {"pattern": "x"}

    outcome = await runner.execute(context, "grep", arguments)
    columns = audit_columns(outcome, context, arguments, decision="auto")

    assert len(json.loads(columns["paths"])) == 20


# ---- ask_question:交互工具在 tool-service 侧只校验+归一,不执行 ----------


async def test_ask_question_returns_interaction_result(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)
    context = _context()
    arguments = {
        "questions": [
            {
                "question": "选择目标环境",
                "header": "环境",
                "options": [
                    {"label": "staging", "description": "预发环境"},
                    {"label": "prod"},
                ],
                "multi_select": True,
            },
            {
                "question": "补充说明?",
                "options": [{"label": "无"}, {"label": "见备注"}],
            },
        ],
        "allow_custom": False,
    }

    outcome = await runner.execute(context, "ask_question", arguments)

    assert outcome.response.ok is True
    assert outcome.response.tool_call_id == "call-1"
    assert outcome.response.idempotent_replay is False
    result = outcome.response.result
    assert result is not None
    assert result["kind"] == "interaction"
    assert result["interaction"] == {"type": "question"}
    normalized = result["normalized"]
    assert normalized["allow_custom"] is False
    assert normalized["questions"] == [
        {
            "question": "选择目标环境",
            "header": "环境",
            "options": [
                {"label": "staging", "description": "预发环境"},
                {"label": "prod"},
            ],
            "multi_select": True,
        },
        {
            "question": "补充说明?",
            "options": [{"label": "无"}, {"label": "见备注"}],
            "multi_select": False,
        },
    ]
    # 交互工具不进沙箱:零 ensure/零 exec。
    assert provider.ensured == []
    assert provider.exec_calls == []
    assert outcome.sandbox_id is None
    columns = audit_columns(outcome, context, arguments, decision="auto")
    assert columns["tool_name"] == "ask_question"
    assert columns["risk"] == "read"
    assert columns["ok"] is True
    assert columns["bytes_written"] == 0
    assert json.loads(columns["paths"]) == []


async def test_ask_question_fills_defaults(tmp_path: Path) -> None:
    runner = _runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(
        _context(),
        "ask_question",
        {"questions": [{"question": "q", "options": [{"label": "a"}, {"label": "b"}]}]},
    )

    normalized = outcome.response.result["normalized"]  # type: ignore[index]
    assert normalized["allow_custom"] is True
    assert normalized["questions"] == [
        {
            "question": "q",
            "options": [{"label": "a"}, {"label": "b"}],
            "multi_select": False,
        }
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"questions": []},
        {
            "questions": [
                {"question": "q", "options": [{"label": "a"}, {"label": "b"}]}
            ]
            * 5
        },
        {"questions": [{"question": "q", "options": []}]},
        {"questions": [{"question": "q"}]},
        {
            "questions": [
                {"question": "q", "options": [{"label": "a"}, {"label": "b"}]}
            ],
            "extra": True,
        },
        {"questions": [{"question": "", "options": [{"label": "a"}, {"label": "b"}]}]},
    ],
)
async def test_ask_question_rejects_invalid_arguments(
    tmp_path: Path, arguments: dict[str, Any]
) -> None:
    provider = FakeSandboxProvider()
    runner = _runner(tmp_path, provider)

    outcome = await runner.execute(_context(), "ask_question", arguments)

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.invalid_args"
    assert provider.ensured == []
    assert provider.exec_calls == []


# ---- search_knowledge:HTTP 执行器代理到知识库(零网络) -------------------


def _http_runner(
    tmp_path: Path,
    provider: FakeSandboxProvider,
    knowledge: FakeKnowledgeClient,
) -> ToolRunner:
    return _runner(tmp_path, provider, http=HttpToolExecutor(knowledge=knowledge))


async def test_search_knowledge_proxies_to_knowledge_client(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    knowledge = FakeKnowledgeClient()
    knowledge.result = {
        "query": "部署",
        "provider": "fake",
        "model": "fake",
        "hits": [{"title": "T", "score": 0.9, "content": "C"}],
    }
    runner = _http_runner(tmp_path, provider, knowledge)
    context = _context()

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
    runner = _http_runner(tmp_path, FakeSandboxProvider(), knowledge)

    await runner.execute(_context(), "search_knowledge", {"query": "q", "limit": 9})

    assert knowledge.calls[0]["limit"] == 9


async def test_search_knowledge_requires_http_executor(tmp_path: Path) -> None:
    runner = _runner(tmp_path, FakeSandboxProvider())

    outcome = await runner.execute(_context(), "search_knowledge", {"query": "q"})

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
    runner = _http_runner(tmp_path, FakeSandboxProvider(), knowledge)

    outcome = await runner.execute(_context(), "search_knowledge", {"query": "q"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == code
    assert outcome.response.error.retryable is retryable
