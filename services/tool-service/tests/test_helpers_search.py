"""glob 与 grep:通配语义、剪枝、二进制/超限跳过与参数拒绝(零网络)。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.helper_support import (
    error_code,
    ok_result,
)

# ---- glob ---------------------------------------------------------------


def test_glob_recursive_matches_nested_paths(workspace: Path) -> None:
    result = ok_result(workspace, "glob", {"pattern": "**/*.py"})
    assert set(result["entries"]) == {"dir/b.py", "dir/sub/c.py"}
    assert result["truncated"] is False


def test_glob_single_segment_star_does_not_cross_dirs(workspace: Path) -> None:
    root = ok_result(workspace, "glob", {"pattern": "*.py"})
    assert root["entries"] == []
    nested = ok_result(workspace, "glob", {"pattern": "*.py", "path": "dir"})
    assert nested["entries"] == ["b.py"]


def test_glob_prunes_denied_and_build_directories(workspace: Path) -> None:
    result = ok_result(workspace, "glob", {"pattern": "**/*", "include_dirs": True})
    entries = set(result["entries"])
    assert "dir" in entries
    assert "dir/sub" in entries
    assert not any(entry.startswith(".git") for entry in entries)
    assert not any(entry.startswith("node_modules") for entry in entries)
    assert not any(entry.startswith("dist") for entry in entries)


def test_glob_skips_symlinks(workspace: Path) -> None:
    result = ok_result(workspace, "glob", {"pattern": "**/*.txt"})
    entries = set(result["entries"])
    assert "a.txt" in entries
    assert "link_in.txt" not in entries
    assert "link_out.txt" not in entries


def test_glob_requires_wildcard(workspace: Path) -> None:
    assert error_code(workspace, "glob", {"pattern": "a.txt"}) == "tool.invalid_args"


def test_glob_rejects_non_directory_base(workspace: Path) -> None:
    assert error_code(workspace, "glob", {"pattern": "*", "path": "a.txt"}) == (
        "tool.not_directory"
    )
    assert error_code(workspace, "glob", {"pattern": "*", "path": "nope"}) == (
        "tool.not_found"
    )


def test_glob_truncates_at_max_results(workspace: Path) -> None:
    result = ok_result(workspace, "glob", {"pattern": "**/*.py", "max_results": 1})
    assert len(result["entries"]) == 1
    assert result["truncated"] is True
    assert result["notes"]


# ---- grep ---------------------------------------------------------------


def test_grep_returns_path_line_column_and_text(workspace: Path) -> None:
    result = ok_result(workspace, "grep", {"pattern": "print"})
    assert result["matches"] == [
        {
            "path": "dir/b.py",
            "line": 2,
            "column": 1,
            "text": "print('b')",
            "context_before": [],
            "context_after": [],
        }
    ]
    assert result["truncated"] is False
    assert result["files_scanned"] >= 2


def test_grep_is_case_insensitive_by_default(workspace: Path) -> None:
    insensitive = ok_result(workspace, "grep", {"pattern": "HELLO"})
    assert [item["path"] for item in insensitive["matches"]] == ["a.txt"]
    sensitive = ok_result(
        workspace, "grep", {"pattern": "HELLO", "case_sensitive": True}
    )
    assert sensitive["matches"] == []


def test_grep_fixed_strings_escapes_regex_metacharacters(workspace: Path) -> None:
    fixed = ok_result(workspace, "grep", {"pattern": "print(", "fixed_strings": True})
    assert [item["path"] for item in fixed["matches"]] == ["dir/b.py"]
    assert error_code(workspace, "grep", {"pattern": "print("}) == "tool.bad_pattern"


def test_grep_context_lines(workspace: Path) -> None:
    result = ok_result(workspace, "grep", {"pattern": "print", "context_lines": 1})
    match = result["matches"][0]
    assert match["context_before"] == ["import os"]
    assert match["context_after"] == [""]


def test_grep_glob_filter(workspace: Path) -> None:
    py_only = ok_result(workspace, "grep", {"pattern": "import", "glob": "*.py"})
    assert [item["path"] for item in py_only["matches"]] == ["dir/b.py"]
    txt_only = ok_result(workspace, "grep", {"pattern": "import", "glob": "*.txt"})
    assert txt_only["matches"] == []


def test_grep_does_not_see_denied_or_pruned_directories(workspace: Path) -> None:
    assert ok_result(workspace, "grep", {"pattern": "TOKEN_NEEDLE"})["matches"] == []
    assert (
        ok_result(workspace, "grep", {"pattern": "repositoryformatversion"})["matches"]
        == []
    )
    assert ok_result(workspace, "grep", {"pattern": "NEEDLE"})["matches"] == []


def test_grep_on_single_file_reports_its_relative_path(workspace: Path) -> None:
    result = ok_result(workspace, "grep", {"pattern": "world", "path": "a.txt"})
    assert [item["path"] for item in result["matches"]] == ["a.txt"]
    assert result["matches"][0]["line"] == 2


def test_grep_skips_binary_and_oversized_files(workspace: Path) -> None:
    result = ok_result(
        workspace, "grep", {"pattern": "hello"}, policy={"max_grep_file_bytes": 10}
    )
    assert result["matches"] == []
    assert result["files_skipped"]["too_large"] >= 1
    assert result["files_skipped"]["binary"] >= 1


def test_grep_truncates_at_max_results(workspace: Path) -> None:
    result = ok_result(
        workspace, "grep", {"pattern": "L", "path": "lines.txt", "max_results": 2}
    )
    assert result["shown"] == 2
    assert result["truncated"] is True


def test_grep_no_match_is_not_an_error(workspace: Path) -> None:
    result = ok_result(workspace, "grep", {"pattern": "zzz-not-here"})
    assert result["ok"] is True
    assert result["matches"] == []
    assert result["truncated"] is False


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"pattern": ""}, "tool.invalid_args"),
        ({"pattern": "a" * 1001}, "tool.pattern_too_long"),
        (
            {
                "pattern": "(",
            },
            "tool.bad_pattern",
        ),
        ({"pattern": "x", "max_results": -1}, "tool.invalid_args"),
        ({"pattern": "x", "context_lines": 9}, "tool.invalid_args"),
        ({"pattern": "x", "path": "nope"}, "tool.not_found"),
    ],
)
def test_grep_rejects_bad_arguments(
    workspace: Path, arguments: dict[str, Any], code: str
) -> None:
    assert error_code(workspace, "grep", arguments) == code
