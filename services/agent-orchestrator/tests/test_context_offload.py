"""大工具结果 offload + read_offloaded 工具单测(零网络)。

覆盖:

- 阈值边界、开关/无上下文降级、落盘路径与 sha256、引用信封字段、max_chars 截断;
- 写沙箱失败降级为原样回填(不丢工具结果);
- `read` 的分页/越界拒读/无上下文/文件缺失;
- `read_offloaded` 的 schema/risk、分页信封与全部错误分支。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from agent_orchestrator.context.offload import (
    DEFAULT_PAGE_CHARS,
    MAX_PAGE_CHARS,
    OFFLOAD_NOTE,
    ToolOutputOffloader,
    build_read_offloaded_tool,
)
from agent_orchestrator.context.policy import ContextPolicy
from agent_orchestrator.services.tool_context import ToolContext, bind_tool_context
from agent_orchestrator.services.tools import ToolRisk
from tests.test_context_agents import TENANT, FakeWorkspaceFiles

PREFIX = "/workspace/.agent/offload"


def _policy(**overrides: object) -> ContextPolicy:
    data: dict[str, object] = {
        "offload_threshold_chars": 10,
        "offload_preview_chars": 5,
    }
    data.update(overrides)
    return ContextPolicy(**data)  # type: ignore[arg-type]


def _bound_context(**overrides: object) -> object:
    data: dict[str, object] = {"tenant_id": TENANT, "run_id": "run-1"}
    data.update(overrides)
    return bind_tool_context(ToolContext(**data))  # type: ignore[arg-type]


# ---- prefix ----


def test_prefix_normalizes_offload_dir() -> None:
    default = ToolOutputOffloader(FakeWorkspaceFiles(), ContextPolicy())
    assert default.prefix == PREFIX
    nested = ToolOutputOffloader(
        FakeWorkspaceFiles(), ContextPolicy(offload_dir="/custom/dir/")
    )
    assert nested.prefix == "/workspace/custom/dir"
    empty = ToolOutputOffloader(FakeWorkspaceFiles(), ContextPolicy(offload_dir=""))
    assert empty.prefix == "/workspace"


# ---- maybe_offload: 不落盘的降级分支 ----


async def test_output_at_threshold_boundary_is_not_offloaded() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy(offload_threshold_chars=10))
    with _bound_context():
        result = await offloader.maybe_offload("read", "x" * 10)
    assert result.content == "x" * 10
    assert result.record is None
    assert files.writes == []


async def test_disabled_offload_returns_output() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy(offload_enabled=False))
    with _bound_context():
        result = await offloader.maybe_offload("read", "x" * 50)
    assert result.content == "x" * 50
    assert result.record is None
    assert files.writes == []


async def test_missing_context_returns_output() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy())
    result = await offloader.maybe_offload("read", "x" * 50)
    assert result.content == "x" * 50
    assert result.record is None
    assert files.writes == []


async def test_empty_tenant_returns_output() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy())
    with bind_tool_context(ToolContext(tenant_id="", run_id="run-1")):
        result = await offloader.maybe_offload("read", "x" * 50)
    assert result.content == "x" * 50
    assert result.record is None
    assert files.writes == []


async def test_write_failure_degrades_to_original_output() -> None:
    files = FakeWorkspaceFiles(fail_write=True)
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context():
        result = await offloader.maybe_offload("read", "x" * 50)
    assert result.content == "x" * 50
    assert result.record is None


# ---- maybe_offload: 成功落盘 ----


async def test_offload_writes_file_and_builds_envelope() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy(offload_preview_chars=4))
    output = "报告" * 10
    with _bound_context(conversation_id="conv-1"):
        result = await offloader.maybe_offload("report_read", output)
    expected_sha = hashlib.sha256(output.encode("utf-8")).hexdigest()
    path = f"{PREFIX}/run-1/report_read-{expected_sha[:8]}.txt"
    assert files.writes == [
        (TENANT, "conversation:conv-1", path, output.encode("utf-8"))
    ]
    envelope = json.loads(result.content)
    assert envelope == {
        "ok": True,
        "offloaded": True,
        "tool": "report_read",
        "path": path,
        "chars": len(output),
        "bytes": len(output.encode("utf-8")),
        "sha256": expected_sha,
        "truncated": False,
        "preview": output[:4],
        "note": OFFLOAD_NOTE,
    }
    assert result.record == {
        "offloaded": True,
        "offload_path": path,
        "original_chars": len(output),
    }


async def test_offload_truncates_stored_content_at_max_chars() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(
        files, _policy(offload_threshold_chars=5, offload_max_chars=6)
    )
    output = "abcdefghij"
    with _bound_context(run_id="run-2"):
        result = await offloader.maybe_offload("grep", output)
    assert files.writes[0][3] == b"abcdef"
    envelope = json.loads(result.content)
    assert envelope["truncated"] is True
    assert envelope["chars"] == 10
    assert envelope["bytes"] == 10
    assert result.record is not None
    assert result.record["original_chars"] == 10


async def test_offload_path_uses_conversation_when_run_missing() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context(run_id=None, conversation_id="conv-1"):
        result = await offloader.maybe_offload("read", "z" * 50)
    assert files.writes[0][1] == "conversation:conv-1"
    assert json.loads(result.content)["path"].startswith(f"{PREFIX}/conv-1/")


async def test_offload_sanitizes_tool_name_and_run_segment() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context(run_id="run/1"):
        result = await offloader.maybe_offload("mcp/server:run", "y" * 50)
    path = json.loads(result.content)["path"]
    assert path.startswith(f"{PREFIX}/run-1/mcp-server-run-")
    assert path.endswith(".txt")


async def test_offload_without_run_or_conversation_uses_anonymous_segment() -> None:
    files = FakeWorkspaceFiles()
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context(run_id=None):
        result = await offloader.maybe_offload("", "q" * 50)
    path = json.loads(result.content)["path"]
    assert path.startswith(f"{PREFIX}/anonymous/tool-")


# ---- read ----


async def test_read_pages_offloaded_file() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "abcdefghij"})
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context():
        chunk, total, has_more = await offloader.read(path, offset=2, limit=3)
        assert (chunk, total, has_more) == ("cde", 10, True)
        tail, total, has_more = await offloader.read(path, offset=8, limit=5)
        assert (tail, total, has_more) == ("ij", 10, False)
        full, total, has_more = await offloader.read(path)
        assert (full, total, has_more) == ("abcdefghij", 10, False)


async def test_read_clamps_negative_offset_and_zero_limit() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "0123456789"})
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context():
        chunk, total, has_more = await offloader.read(path, offset=-3, limit=0)
    assert (chunk, total, has_more) == ("0", 10, True)


async def test_read_normalizes_dot_segments_within_prefix() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "abc"})
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context():
        text, total, has_more = await offloader.read(f"{PREFIX}/run-1/../run-1/a.txt")
    assert (text, total, has_more) == ("abc", 3, False)


async def test_read_rejects_paths_outside_prefix() -> None:
    offloader = ToolOutputOffloader(FakeWorkspaceFiles(), _policy())
    with _bound_context(), pytest.raises(ValueError, match="路径不在 offload 目录内"):
        await offloader.read("/workspace/secret.txt")
    with _bound_context(), pytest.raises(ValueError, match="路径不在 offload 目录内"):
        await offloader.read(f"{PREFIX}/../secret.txt")


async def test_read_requires_tool_context() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    offloader = ToolOutputOffloader(FakeWorkspaceFiles({path: "abc"}), _policy())
    with pytest.raises(ValueError, match="缺少工具上下文"):
        await offloader.read(path)


async def test_read_missing_file_raises_value_error() -> None:
    offloader = ToolOutputOffloader(FakeWorkspaceFiles(), _policy())
    with _bound_context(), pytest.raises(ValueError, match="不存在"):
        await offloader.read(f"{PREFIX}/run-1/none.txt")


async def test_read_failure_is_translated_to_value_error() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "abc"}, raise_read=True)
    offloader = ToolOutputOffloader(files, _policy())
    with _bound_context(), pytest.raises(ValueError, match="读取 offload 文件失败"):
        await offloader.read(path)


# ---- build_read_offloaded_tool ----


async def test_read_offloaded_tool_metadata_and_page_envelope() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "0123456789"})
    offloader = ToolOutputOffloader(files, _policy())
    tool = build_read_offloaded_tool(offloader)
    assert tool.name == "read_offloaded"
    assert tool.risk is ToolRisk.READ
    assert tool.parameters["type"] == "object"
    assert tool.parameters["required"] == ["path"]
    assert tool.parameters["additionalProperties"] is False
    assert tool.parameters["properties"]["offset"]["default"] == 0
    assert tool.parameters["properties"]["limit"]["default"] == DEFAULT_PAGE_CHARS

    with _bound_context():
        payload = json.loads(
            await tool.handler({"path": f"  {path}  ", "offset": 2, "limit": 3})
        )
        assert payload == {
            "path": path,
            "offset": 2,
            "limit": 3,
            "content": "234",
            "total_chars": 10,
            "truncated": True,
        }

        default_payload = json.loads(await tool.handler({"path": path}))
        assert default_payload["offset"] == 0
        assert default_payload["limit"] == DEFAULT_PAGE_CHARS
        assert default_payload["content"] == "0123456789"
        assert default_payload["truncated"] is False


async def test_read_offloaded_tool_clamps_args_and_ignores_bad_types() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "0123456789"})
    offloader = ToolOutputOffloader(files, _policy())
    tool = build_read_offloaded_tool(offloader)

    with _bound_context():
        clamped = json.loads(
            await tool.handler({"path": path, "offset": -5, "limit": 0})
        )
        assert clamped["offset"] == 0
        assert clamped["limit"] == 1
        assert clamped["content"] == "0"

        capped = json.loads(await tool.handler({"path": path, "limit": 99999}))
        assert capped["limit"] == MAX_PAGE_CHARS

        defaults = json.loads(
            await tool.handler({"path": path, "offset": "2", "limit": None})
        )
        assert defaults["offset"] == 0
        assert defaults["limit"] == DEFAULT_PAGE_CHARS


async def test_read_offloaded_tool_error_branches() -> None:
    path = f"{PREFIX}/run-1/a.txt"
    files = FakeWorkspaceFiles({path: "abc"})
    offloader = ToolOutputOffloader(files, _policy())
    tool = build_read_offloaded_tool(offloader)

    # 无工具上下文
    payload = json.loads(await tool.handler({"path": path}))
    assert payload["error"] == "缺少工具上下文(租户),无法读取 offload 文件"

    with _bound_context():
        # 缺 path / 空 path / 非字符串 path
        for args in ({}, {"path": "   "}, {"path": 123}):
            payload = json.loads(await tool.handler(args))
            assert payload["error"] == "参数 path 不能为空"

        # 越界路径
        payload = json.loads(await tool.handler({"path": "/workspace/secret.txt"}))
        assert "路径不在 offload 目录内" in payload["error"]

        # 文件不存在
        payload = json.loads(await tool.handler({"path": f"{PREFIX}/run-1/none.txt"}))
        assert "不存在" in payload["error"]


async def test_read_offloaded_tool_wraps_unexpected_errors() -> None:
    class _ExplodingOffloader:
        async def read(self, path: str, *, offset: int = 0, limit: int = 1):
            raise RuntimeError("炸了")

    tool = build_read_offloaded_tool(_ExplodingOffloader())  # type: ignore[arg-type]
    with _bound_context():
        payload = json.loads(await tool.handler({"path": f"{PREFIX}/run-1/a.txt"}))
    assert payload["error"] == "读取 offload 文件失败: 炸了"
