"""list_dir 与 stdin 协议层:目录排序/深度/截断与非法请求信封(零网络)。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.helper_support import (
    error_code,
    ok_result,
    run_helper,
)

# ---- list_dir -----------------------------------------------------------


def test_list_dir_shows_dirs_first_then_files(workspace: Path) -> None:
    result = ok_result(workspace, "list_dir", {})
    names = [entry["name"] for entry in result["entries"]]
    assert names == [
        "dir",
        "dist",
        "a.txt",
        "bin.dat",
        "bom.txt",
        "empty.txt",
        "lines.txt",
        "long_line.txt",
        "u16.txt",
    ]
    assert result["entries"][0]["type"] == "dir"
    assert result["entries"][1]["type"] == "dir"
    assert result["entries"][2]["type"] == "file"
    assert result["entries"][2]["size"] == 12
    assert result["entries"][2]["modified_at"]
    assert result["truncated"] is False


def test_list_dir_hides_dotfiles_unless_requested(workspace: Path) -> None:
    default = ok_result(workspace, "list_dir", {})
    assert ".hidden.txt" not in [entry["name"] for entry in default["entries"]]
    hidden = ok_result(workspace, "list_dir", {"include_hidden": True})
    names = [entry["name"] for entry in hidden["entries"]]
    assert ".hidden.txt" in names
    assert ".git" not in names


def test_list_dir_respects_depth(workspace: Path) -> None:
    result = ok_result(workspace, "list_dir", {"path": "dir", "depth": 2})
    names = [entry["name"] for entry in result["entries"]]
    assert names == ["sub", "b.py", "sub/c.py"]


def test_list_dir_truncates_at_max_entries(workspace: Path) -> None:
    result = ok_result(workspace, "list_dir", {"max_entries": 1})
    assert len(result["entries"]) == 1
    assert result["truncated"] is True


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"depth": -1}, "tool.invalid_args"),
        ({"depth": 4}, "tool.invalid_args"),
        ({"max_entries": -1}, "tool.invalid_args"),
        ({"path": "nope"}, "tool.not_found"),
        ({"path": "a.txt"}, "tool.not_directory"),
    ],
)
def test_list_dir_rejects_bad_arguments(
    workspace: Path, arguments: dict[str, Any], code: str
) -> None:
    assert error_code(workspace, "list_dir", arguments) == code


# ---- 协议层 -------------------------------------------------------------


def test_unknown_tool_returns_error_envelope(workspace: Path) -> None:
    result = run_helper(workspace, "nope", {"path": "x"})
    assert result["ok"] is False
    assert result["error"]["code"] == "tool.unknown_tool"


def test_invalid_stdin_json_returns_error_envelope(workspace: Path) -> None:
    result = run_helper(workspace, "read", stdin="{not json")
    assert result["ok"] is False
    assert result["error"]["code"] == "tool.invalid_args"


def test_non_object_stdin_is_rejected(workspace: Path) -> None:
    result = run_helper(workspace, "read", stdin="[1, 2]")
    assert result["ok"] is False
    assert result["error"]["code"] == "tool.invalid_args"


def test_non_object_arguments_are_rejected(workspace: Path) -> None:
    result = run_helper(
        workspace,
        "read",
        stdin=json.dumps({"tool": "read", "arguments": [], "policy": {}}),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "tool.invalid_args"
