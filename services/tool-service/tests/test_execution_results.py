"""沙箱执行结果处理:helper 错误信封、大小上限与服务端补丁剥离(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import FakeSandboxProvider
from tests.execution_support import execution_context, make_runner
from tool_service.constants import MAX_TOOL_CHARS
from tool_service.services.execution import (
    audit_columns,
)

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
    runner = make_runner(tmp_path, provider)
    context = execution_context(approved=True)
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
    runner = make_runner(tmp_path, provider)
    context = execution_context(approved=True)

    outcome = await runner.execute(context, "write", {"path": "a.txt", "content": "hi"})

    assert outcome.response.ok is True
    assert outcome.patch is not None
    assert outcome.patch.patch == huge_patch
    assert "patch" not in (outcome.response.result or {})


async def test_read_result_has_no_patch_audit(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    runner = make_runner(tmp_path, provider)
    context = execution_context()

    outcome = await runner.execute(context, "read", {"path": "a.txt"})
    columns = audit_columns(outcome, context, {"path": "a.txt"}, decision="auto")

    assert outcome.patch is None
    assert columns["patch"] is None
    assert columns["lines_added"] is None
    assert columns["lines_removed"] is None
    assert columns["patch_truncated"] is False


async def test_helper_exit_code_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.exit_code = 1
    provider.stdout = ""
    provider.stderr = "traceback: boom"
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"
    assert "boom" in outcome.response.error.hint
    assert outcome.response.error.retryable is True


async def test_helper_bad_json_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = "not json"
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"


async def test_helper_non_envelope_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"tool": "read"})
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.io_error"
    assert "信封" in outcome.response.error.message


async def test_helper_unparsable_envelope_becomes_io_error(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"ok": True, "duration_ms": "abc"})
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

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
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.not_found"


async def test_oversized_result_becomes_too_large(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"ok": True, "content": "x" * 30000})
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is False
    assert outcome.response.error is not None
    assert outcome.response.error.code == "tool.too_large"


async def test_result_under_limit_is_returned(tmp_path: Path) -> None:
    provider = FakeSandboxProvider()
    provider.stdout = json.dumps({"ok": True, "content": "x" * 100})
    runner = make_runner(tmp_path, provider)

    outcome = await runner.execute(execution_context(), "read", {"path": "a.txt"})

    assert outcome.response.ok is True
    assert outcome.response.result == {"content": "x" * 100}
