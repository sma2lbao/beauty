"""write 与 edit:读写门禁、sha 冲突、符号链接/硬链接与编码保护(零网络)。"""

from __future__ import annotations

import os
from pathlib import Path

from tests.helper_support import (
    error_code,
    ok_result,
    run_helper,
    sha256_of,
)

# ---- write --------------------------------------------------------------


def test_write_creates_file(workspace: Path) -> None:
    result = ok_result(workspace, "write", {"path": "new.txt", "content": "abc\n"})

    assert result["created"] is True
    assert result["changed"] is True
    assert result["bytes"] == 4
    assert result["lines"] == 1
    assert result["sha256_before"] is None
    assert result["sha256_after"] == sha256_of("abc\n")
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "abc\n"


def test_write_overwrites_existing(workspace: Path) -> None:
    result = ok_result(
        workspace, "write", {"path": "a.txt", "content": "new content\n"}
    )

    assert result["created"] is False
    assert result["changed"] is True
    assert result["sha256_before"] == sha256_of("hello\nworld\n")
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "new content\n"


def test_write_same_content_is_noop(workspace: Path) -> None:
    result = ok_result(
        workspace, "write", {"path": "a.txt", "content": "hello\nworld\n"}
    )

    assert result["created"] is False
    assert result["changed"] is False
    assert result["sha256_before"] == result["sha256_after"]
    assert any("未写入" in note for note in result["notes"])


def test_write_overwrite_false_rejects_existing(workspace: Path) -> None:
    code = error_code(
        workspace,
        "write",
        {"path": "a.txt", "content": "x", "overwrite": False},
    )

    assert code == "tool.exists"
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_write_expected_sha_conflict(workspace: Path) -> None:
    code = error_code(
        workspace,
        "write",
        {"path": "a.txt", "content": "x", "expected_sha256": "f" * 64},
    )

    assert code == "tool.conflict"


def test_write_stale_read_sha_rejected(workspace: Path) -> None:
    """内部读前置校验参数不匹配 → tool.stale_read(需重新 read)。"""

    code = error_code(
        workspace,
        "write",
        {"path": "a.txt", "content": "x", "_expected_read_sha256": "f" * 64},
    )

    assert code == "tool.stale_read"


def test_write_read_gate_absent_new_file_is_created(workspace: Path) -> None:
    """读前置放行新建:目标不存在 → 直接创建(内部标记只放行「新建」)。"""

    result = ok_result(
        workspace,
        "write",
        {"path": "fresh.txt", "content": "abc\n", "_read_gate_absent": True},
    )

    assert result["created"] is True
    assert result["changed"] is True
    assert (workspace / "fresh.txt").read_text(encoding="utf-8") == "abc\n"


def test_write_read_gate_absent_existing_file_rejected(workspace: Path) -> None:
    """读前置放行的只是新建:目标已存在仍然回 tool.read_required(防盲改)。"""

    code = error_code(
        workspace,
        "write",
        {"path": "a.txt", "content": "x", "_read_gate_absent": True},
    )

    assert code == "tool.read_required"
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_write_create_parents_false_rejects_missing_parent(workspace: Path) -> None:
    code = error_code(
        workspace,
        "write",
        {"path": "deep/nested/new.txt", "content": "x", "create_parents": False},
    )

    assert code == "tool.no_parent"


def test_write_create_parents_default_creates_dirs(workspace: Path) -> None:
    result = ok_result(
        workspace, "write", {"path": "deep/nested/new.txt", "content": "x\n"}
    )

    assert result["created"] is True
    assert (workspace / "deep" / "nested" / "new.txt").read_text() == "x\n"


def test_write_denies_secret_file_names(workspace: Path) -> None:
    code = error_code(workspace, "write", {"path": ".env", "content": "A=1"})

    assert code == "tool.denied"
    assert not (workspace / ".env").exists()


def test_write_rejects_symlink_target(workspace: Path) -> None:
    code = error_code(
        workspace, "write", {"path": "link_in.txt", "content": "overwritten"}
    )

    assert code == "tool.symlink_denied"
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_write_rejects_hardlink_target(workspace: Path) -> None:
    os.link(workspace / "a.txt", workspace / "hard.txt")

    code = error_code(workspace, "write", {"path": "hard.txt", "content": "x"})

    assert code == "tool.hardlink_denied"


def test_write_rejects_absolute_and_escape_paths(workspace: Path) -> None:
    assert (
        error_code(workspace, "write", {"path": "/tmp/x", "content": "x"})
        == "tool.invalid_path"
    )
    assert (
        error_code(workspace, "write", {"path": "../x.txt", "content": "x"})
        == "tool.path_escaped"
    )


# ---- edit ---------------------------------------------------------------


def test_edit_replaces_unique_match(workspace: Path) -> None:
    result = ok_result(
        workspace,
        "edit",
        {"path": "lines.txt", "old_string": "L3", "new_string": "L3X"},
    )

    assert result["replacements"] == 1
    assert result["sha256_before"] == sha256_of("L1\nL2\nL3\nL4\nL5\n")
    assert (workspace / "lines.txt").read_text() == "L1\nL2\nL3X\nL4\nL5\n"


def test_edit_ambiguous_without_replace_all(workspace: Path) -> None:
    result = run_helper(
        workspace,
        "edit",
        {"path": "lines.txt", "old_string": "L", "new_string": "X"},
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "tool.edit_ambiguous"
    assert "5" in result["error"]["message"]
    assert "replace_all" in result["error"]["hint"]
    assert (workspace / "lines.txt").read_text() == "L1\nL2\nL3\nL4\nL5\n"


def test_edit_replace_all(workspace: Path) -> None:
    result = ok_result(
        workspace,
        "edit",
        {
            "path": "lines.txt",
            "old_string": "L",
            "new_string": "X",
            "replace_all": True,
        },
    )

    assert result["replacements"] == 5
    assert (workspace / "lines.txt").read_text() == "X1\nX2\nX3\nX4\nX5\n"


def test_edit_no_match_hints_crlf(tmp_path: Path) -> None:
    root = tmp_path / "crlf"
    root.mkdir()
    (root / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    result = run_helper(
        root, "edit", {"path": "crlf.txt", "old_string": "one\ntwo", "new_string": "x"}
    )

    assert result["error"]["code"] == "tool.edit_no_match"
    assert "CRLF" in result["error"]["hint"]


def test_edit_expected_sha_conflict(workspace: Path) -> None:
    code = error_code(
        workspace,
        "edit",
        {
            "path": "a.txt",
            "old_string": "hello",
            "new_string": "hi",
            "expected_sha256": "f" * 64,
        },
    )

    assert code == "tool.conflict"


def test_edit_no_change_rejected(workspace: Path) -> None:
    code = error_code(
        workspace,
        "edit",
        {"path": "a.txt", "old_string": "hello", "new_string": "hello"},
    )

    assert code == "tool.no_change"


def test_edit_preserves_utf8_bom(workspace: Path) -> None:
    result = ok_result(
        workspace, "edit", {"path": "bom.txt", "old_string": "hi", "new_string": "yo"}
    )

    assert result["replacements"] == 1
    assert (workspace / "bom.txt").read_bytes() == b"\xef\xbb\xbfyo\n"


def test_edit_rejects_binary_file(workspace: Path) -> None:
    code = error_code(
        workspace, "edit", {"path": "bin.dat", "old_string": "a", "new_string": "b"}
    )

    assert code == "tool.binary_file"
