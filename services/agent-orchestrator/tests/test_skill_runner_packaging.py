"""UNPACK_PY 本地联调(真实子进程、零网络):恶意 zip 一律拒绝。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.services.skill_runner import UNPACK_PY
from tests.support.skill_runner import SKILL_MD, build_zip


def _run_unpack(
    tmp_path: Path, data: bytes
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    script = tmp_path / "unpack.py"
    script.write_text(UNPACK_PY, encoding="utf-8")
    archive = tmp_path / "package.zip"
    archive.write_bytes(data)
    completed = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps({"zip": str(archive), "dest": str(tmp_path / "out")}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed, json.loads(completed.stdout)


def test_unpack_py_extracts_single_root_package(tmp_path: Path) -> None:
    data = build_zip(
        {
            "weekly/SKILL.md": SKILL_MD,
            "weekly/scripts/run.py": "print(1)",
        }
    )
    completed, payload = _run_unpack(tmp_path, data)
    assert completed.returncode == 0
    assert payload["ok"] is True
    assert (tmp_path / "out" / "SKILL.md").read_text() == SKILL_MD
    assert (tmp_path / "out" / "scripts" / "run.py").read_text() == "print(1)"


@pytest.mark.parametrize(
    ("files", "symlinks", "code"),
    [
        ({"SKILL.md": SKILL_MD, "../evil.txt": "x"}, None, "path_invalid"),
        ({"SKILL.md": SKILL_MD, "/abs.txt": "x"}, None, "path_invalid"),
        ({"SKILL.md": SKILL_MD, "C:/evil.txt": "x"}, None, "path_invalid"),
        ({"SKILL.md": SKILL_MD}, {"link": "/etc/passwd"}, "path_invalid"),
    ],
)
def test_unpack_py_rejects_unsafe_paths(
    tmp_path: Path,
    files: dict[str, str],
    symlinks: dict[str, str] | None,
    code: str,
) -> None:
    completed, payload = _run_unpack(tmp_path, build_zip(files, symlinks=symlinks))
    assert completed.returncode == 2
    assert payload["ok"] is False
    assert payload["code"] == code
    assert not (tmp_path / "evil.txt").exists()


def test_unpack_py_rejects_too_many_files(tmp_path: Path) -> None:
    files = {"SKILL.md": SKILL_MD}
    files.update({f"f{i}.txt": "x" for i in range(501)})
    completed, payload = _run_unpack(tmp_path, build_zip(files))
    assert completed.returncode == 2
    assert payload["code"] == "too_large"
