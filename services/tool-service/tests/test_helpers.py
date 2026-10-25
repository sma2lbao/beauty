"""沙箱 helper 语义与安全:subprocess 真跑 helpers/fs_helper.py 四件套。

helper 在沙箱里由 python3 -c <源码> 启动、请求 JSON 走 stdin;这里用
sys.executable + 同一条 stdin 协议直测,既能覆盖语义,也能覆盖路径逃逸/
拒绝名单/剪枝等安全边界,不需要 microsandbox。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tool_service.constants import build_policy
from tool_service.services.helpers import HELPER_PATH

NUMBERED_HELLO = "     1\thello\n     2\tworld"


def run_helper(
    root: Path,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    policy: dict[str, Any] | None = None,
    stdin: str | None = None,
) -> dict[str, Any]:
    """按线上协议跑 helper:stdin 传 {tool,arguments,policy}。"""

    if stdin is None:
        payload = {
            "tool": tool,
            "arguments": arguments or {},
            "policy": {**build_policy(), **(policy or {})},
        }
        stdin = json.dumps(payload, ensure_ascii=False)
    proc = subprocess.run(
        [sys.executable, str(HELPER_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "BM_WORKSPACE_ROOT": str(root)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def ok_result(
    root: Path,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = run_helper(root, tool, arguments, policy=policy)
    assert result["ok"] is True, result
    return result


def error_code(
    root: Path,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    policy: dict[str, Any] | None = None,
) -> str:
    result = run_helper(root, tool, arguments, policy=policy)
    assert result["ok"] is False, result
    return result["error"]["code"]


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (root / "lines.txt").write_text("L1\nL2\nL3\nL4\nL5\n", encoding="utf-8")
    (root / "empty.txt").write_text("", encoding="utf-8")
    (root / "bom.txt").write_bytes(b"\xef\xbb\xbfhi\n")
    (root / "long_line.txt").write_text("y" * 200 + "\n", encoding="utf-8")
    (root / ".hidden.txt").write_text("hidden\n", encoding="utf-8")
    (root / "bin.dat").write_bytes(b"a\x00b")
    (root / "u16.txt").write_bytes("hi".encode("utf-16"))
    (root / "dir").mkdir()
    (root / "dir" / "b.py").write_text("import os\nprint('b')\n", encoding="utf-8")
    (root / "dir" / "sub").mkdir()
    (root / "dir" / "sub" / "c.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text(
        "[core]\nrepositoryformatversion = 0\n", encoding="utf-8"
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.js").write_text(
        "TOKEN_NEEDLE = 1\n", encoding="utf-8"
    )
    (root / "dist").mkdir()
    (root / "dist" / "bundle.js").write_text("NEEDLE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (root / "link_out.txt").symlink_to(outside)
    (root / "link_in.txt").symlink_to(root / "a.txt")
    return root


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
    result = run_helper(workspace, "write", {"path": "x", "content": "y"})
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
