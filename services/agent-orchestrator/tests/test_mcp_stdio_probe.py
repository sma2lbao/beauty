"""stdio probe 本地联调(真实子进程,零网络)。"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.services.mcp_stdio import MARKER, PROBE_JS, PROBE_PY
from tests.support.mcp_stdio import DUMMY_JS, DUMMY_PY, SLOW_PY


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _run_probe(
    executable: str, probe_path: Path, request: dict[str, Any]
) -> dict[str, Any]:
    completed = subprocess.run(
        [executable, str(probe_path)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip().startswith(MARKER)
    ]
    assert lines, (
        f"probe 没有输出 marker;stdout={completed.stdout} stderr={completed.stderr}"
    )
    payload = json.loads(lines[-1][len(MARKER) :])
    assert completed.returncode == 0
    return payload


def test_probe_py_lists_and_calls(tmp_path: Path) -> None:
    probe = _write(tmp_path, "probe.py", PROBE_PY)
    dummy = _write(tmp_path, "dummy.py", DUMMY_PY)
    base = {
        "command": sys.executable,
        "args": [str(dummy)],
        "env": {"BEAUTY_TEST_ENV": "42"},
        "timeout": 10,
    }
    listed = _run_probe(sys.executable, probe, {**base, "action": "tools/list"})
    assert listed["ok"] is True
    assert listed["result"]["tools"][0]["name"] == "echo"

    called = _run_probe(
        sys.executable,
        probe,
        {
            **base,
            "action": "tools/call",
            "tool": "echo",
            "arguments": {"text": "hi"},
        },
    )
    assert called["ok"] is True
    assert called["result"]["content"][0]["text"] == "echo:hi env:42"


def test_probe_py_reports_spawn_failure(tmp_path: Path) -> None:
    probe = _write(tmp_path, "probe.py", PROBE_PY)
    payload = _run_probe(
        sys.executable,
        probe,
        {
            "command": str(tmp_path / "not-a-real-binary"),
            "args": [],
            "timeout": 5,
            "action": "tools/list",
        },
    )
    assert payload["ok"] is False
    assert payload["error"]


def test_probe_py_times_out(tmp_path: Path) -> None:
    probe = _write(tmp_path, "probe.py", PROBE_PY)
    dummy = _write(tmp_path, "slow.py", SLOW_PY)
    payload = _run_probe(
        sys.executable,
        probe,
        {
            "command": sys.executable,
            "args": [str(dummy)],
            "timeout": 0.5,
            "action": "tools/list",
        },
    )
    assert payload["ok"] is False
    assert "超时" in payload["error"]


def test_probe_js_lists_and_calls(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 不可用")
    probe = _write(tmp_path, "probe.js", PROBE_JS)
    dummy = _write(tmp_path, "dummy.js", DUMMY_JS)
    base = {
        "command": node,
        "args": [str(dummy)],
        "env": {"BEAUTY_TEST_ENV": "42"},
        "timeout": 10,
    }
    listed = _run_probe(node, probe, {**base, "action": "tools/list"})
    assert listed["ok"] is True
    assert listed["result"]["tools"][0]["name"] == "echo"

    called = _run_probe(
        node,
        probe,
        {
            **base,
            "action": "tools/call",
            "tool": "echo",
            "arguments": {"text": "hi"},
        },
    )
    assert called["ok"] is True
