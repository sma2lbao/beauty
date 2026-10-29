"""read:行号、偏移、BOM、超长行与路径安全(真跑 fs_helper.py,零网络)。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from tests.helper_support import (
    NUMBERED_HELLO,
    error_code,
    ok_result,
)

# ---- read ---------------------------------------------------------------


def test_read_returns_numbered_lines(workspace: Path) -> None:
    result = ok_result(workspace, "read", {"path": "a.txt"})
    assert result["path"] == "a.txt"
    assert result["total_lines"] == 2
    assert result["offset"] == 1
    assert result["returned_lines"] == 2
    assert result["truncated"] is False
    assert result["next_offset"] is None
    assert result["bytes"] == 12
    assert result["encoding"] == "utf-8"
    assert result["bom"] is False
    assert result["ends_with_newline"] is True
    assert result["sha256"] == hashlib.sha256(b"hello\nworld\n").hexdigest()
    assert result["content"] == NUMBERED_HELLO
    assert result["notes"] == []


def test_read_offset_and_limit_give_next_offset(workspace: Path) -> None:
    result = ok_result(
        workspace, "read", {"path": "lines.txt", "offset": 2, "limit": 2}
    )
    assert result["returned_lines"] == 2
    assert result["truncated"] is True
    assert result["next_offset"] == 4
    assert result["content"] == "     2\tL2\n     3\tL3"


def test_read_offset_beyond_end_reports_note(workspace: Path) -> None:
    result = ok_result(workspace, "read", {"path": "lines.txt", "offset": 99})
    assert result["returned_lines"] == 0
    assert result["content"] == ""
    assert result["truncated"] is False
    assert "offset 超出文件行数" in result["notes"]


def test_read_empty_file(workspace: Path) -> None:
    result = ok_result(workspace, "read", {"path": "empty.txt"})
    assert result["total_lines"] == 0
    assert result["bytes"] == 0
    assert result["content"] == ""


def test_read_strips_utf8_bom(workspace: Path) -> None:
    result = ok_result(workspace, "read", {"path": "bom.txt"})
    assert result["bom"] is True
    assert result["content"] == "     1\thi"


def test_read_truncates_long_lines_by_char_budget(workspace: Path) -> None:
    result = ok_result(
        workspace, "read", {"path": "long_line.txt", "max_line_chars": 120}
    )
    body = result["content"].split("\t", 1)[1]
    assert body.endswith("\u2026")
    assert len(body) == 121


def test_read_resolves_symlink_inside_workspace(workspace: Path) -> None:
    result = ok_result(workspace, "read", {"path": "link_in.txt"})
    assert result["path"] == "a.txt"
    assert result["content"] == NUMBERED_HELLO


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("dir", "tool.is_directory"),
        ("missing.txt", "tool.not_found"),
        ("bin.dat", "tool.binary_file"),
        ("u16.txt", "tool.unsupported_encoding"),
    ],
)
def test_read_rejects_unsupported_targets(
    workspace: Path, path: str, code: str
) -> None:
    assert error_code(workspace, "read", {"path": path}) == code


def test_read_rejects_file_above_size_limit(workspace: Path) -> None:
    code = error_code(
        workspace, "read", {"path": "a.txt"}, policy={"max_read_file_bytes": 5}
    )
    assert code == "tool.too_large"


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "a.txt", "limit": -1},
        {"path": "a.txt", "limit": 2001},
        {"path": "a.txt", "offset": -2},
        {"path": "a.txt", "max_line_chars": 10},
        {"path": 1},
        {},
    ],
)
def test_read_rejects_bad_arguments(workspace: Path, arguments: dict[str, Any]) -> None:
    assert error_code(workspace, "read", arguments) == "tool.invalid_args"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("../outside.txt", "tool.path_escaped"),
        ("/etc/passwd", "tool.invalid_path"),
        ("~/secret", "tool.invalid_path"),
        ("a\x00b", "tool.invalid_path"),
        ("a\x01b", "tool.invalid_path"),
        ("a" * 513, "tool.path_too_long"),
        (".git/config", "tool.denied"),
        ("link_out.txt", "tool.path_escaped"),
        ("dir/../a.txt", "tool.path_escaped"),
    ],
)
def test_read_rejects_path_escapes(workspace: Path, path: str, code: str) -> None:
    assert error_code(workspace, "read", {"path": path}) == code
