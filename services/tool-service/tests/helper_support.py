"""沙箱 helper 测试共享装配:按线上 stdin 协议直跑 fs_helper.py 的助手。

helper 在沙箱里由 `python3 -c <源码>` 启动、请求 JSON 走 stdin;测试用
sys.executable + 同一条 stdin 协议直测,因此这里只封装协议本身与常见的
"成功/错误码/sha256" 断言口径。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
