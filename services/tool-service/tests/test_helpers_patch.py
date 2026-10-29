"""服务端补丁:write/edit 结果里的 patch 字段生成、上限与不落模型的边界。"""

from __future__ import annotations

from pathlib import Path

from tests.helper_support import (
    ok_result,
)

# ---- 服务端补丁(write/edit 结果里的 patch 字段) ------------------------


def test_write_new_file_patch_is_all_additions(workspace: Path) -> None:
    result = ok_result(
        workspace, "write", {"path": "brand-new.txt", "content": "one\ntwo\n"}
    )

    assert result["created"] is True
    patch = result["patch"]
    assert patch.startswith("--- /dev/null\n+++ b/brand-new.txt\n")
    body = [
        line
        for line in patch.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    assert body == ["+one", "+two"]
    assert result["lines_added"] == 2
    assert result["lines_removed"] == 0
    assert result["patch_truncated"] is False


def test_write_existing_file_patch_has_context(workspace: Path) -> None:
    result = ok_result(
        workspace, "write", {"path": "a.txt", "content": "hello\nthere\n"}
    )

    patch = result["patch"]
    assert patch.startswith("--- a/a.txt\n+++ b/a.txt\n")
    assert "@@ -1,2 +1,2 @@" in patch
    assert "-world" in patch.splitlines()
    assert "+there" in patch.splitlines()
    assert result["lines_added"] == 1
    assert result["lines_removed"] == 1
    assert result["patch_truncated"] is False


def test_write_patch_is_not_truncated_past_event_limit(workspace: Path) -> None:
    """补丁不再受 2000 字符事件预算限制:长文件也要完整落审计。"""

    before = "".join(f"line-{index:03d} old\n" for index in range(120))
    after = "".join(f"line-{index:03d} new\n" for index in range(120))
    (workspace / "long.txt").write_text(before, encoding="utf-8")

    result = ok_result(workspace, "write", {"path": "long.txt", "content": after})

    assert result["patch_truncated"] is False
    assert len(result["patch"]) > 2000
    assert "+line-000 new" in result["patch"].splitlines()
    assert "-line-000 old" in result["patch"].splitlines()
    assert result["lines_added"] == 120
    assert result["lines_removed"] == 120


def test_write_patch_is_capped_at_64kb(workspace: Path) -> None:
    (workspace / "huge.txt").write_text("OLD\n", encoding="utf-8")
    after = "".join(f"new-{index:05d}\n" for index in range(8000))

    result = ok_result(workspace, "write", {"path": "huge.txt", "content": after})

    assert result["patch_truncated"] is True
    assert len(result["patch"]) == 64 * 1024
    assert result["lines_added"] == 8000
    assert result["lines_removed"] == 1


def test_write_unchanged_file_has_no_patch_fields(workspace: Path) -> None:
    result = ok_result(
        workspace, "write", {"path": "a.txt", "content": "hello\nworld\n"}
    )

    assert result["changed"] is False
    assert "patch" not in result


def test_write_skips_patch_when_old_text_is_not_decodable(workspace: Path) -> None:
    result = ok_result(workspace, "write", {"path": "bin.dat", "content": "text\n"})

    assert result["ok"] is True
    assert result["changed"] is True
    assert "patch" not in result


def test_edit_patch_is_exact(workspace: Path) -> None:
    result = ok_result(
        workspace,
        "edit",
        {"path": "lines.txt", "old_string": "L3", "new_string": "L3X"},
    )

    patch = result["patch"]
    lines = patch.splitlines()
    assert lines[0] == "--- a/lines.txt"
    assert lines[1] == "+++ b/lines.txt"
    assert "-L3" in lines
    assert "+L3X" in lines
    assert result["lines_added"] == 1
    assert result["lines_removed"] == 1
    assert result["patch_truncated"] is False
