"""skill 运行器公共类型与工具函数(自 skill_runner.py 拆出)。"""

from __future__ import annotations

import json
import mimetypes
from dataclasses import dataclass
from typing import Any

from agent_orchestrator.services import artifacts
from agent_orchestrator.services.sandbox import SandboxExecResult

INTERPRETERS = frozenset(
    {
        "python",
        "python3",
        "python3.11",
        "python3.12",
        "python3.13",
        "py",
        "node",
        "nodejs",
        "bash",
        "sh",
    }
)


RUNTIME_BINS = {"python": "python3", "node": "node"}


MARKDOWN_SUFFIXES = (".md", ".markdown")


class SkillRunError(Exception):
    """技能包执行失败(可读原因;execute 转成 {"error": ...} 返回)。"""


@dataclass
class OutputFile:
    """产物目录里的一个文件(未超限时读到字节,文本额外解码出正文)。"""

    path: str
    size: int
    text: str | None = None
    binary: bool = False
    truncated: bool = False
    data: bytes | None = None
    artifact_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": self.path,
            "size": self.size,
            "binary": self.binary,
            "truncated": self.truncated,
        }
        if self.artifact_id is not None:
            payload["artifact_id"] = self.artifact_id
        return payload


def _error(message: str) -> str:
    return json.dumps({"error": message}, ensure_ascii=False)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    """按字符截断;返回 (文本, 是否截断)。"""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _entrypoint_argv(entrypoint: list[str] | None, runtime: str) -> list[str]:
    """解析入口命令:runtime 选解释器,entrypoint 首段已是解释器则原样用。

    上传时已拒绝绝对路径/穿越;这里再守一遍(DB 快照不能当作可信输入),
    避免沙箱内执行被快照改动带偏。
    """
    argv = [str(arg) for arg in entrypoint or []]
    if not argv:
        raise SkillRunError("该技能包没有 entrypoint,不能执行")
    for arg in argv:
        if "\x00" in arg:
            raise SkillRunError("entrypoint 含 NUL 字符")
        candidate = arg.replace("\\", "/")
        if candidate.startswith("/") or (len(candidate) > 1 and candidate[1] == ":"):
            raise SkillRunError(f"entrypoint 不允许绝对路径:{arg}")
        if ".." in candidate.split("/"):
            raise SkillRunError(f"entrypoint 不允许路径穿越:{arg}")
    if argv[0] in INTERPRETERS:
        return argv
    return [RUNTIME_BINS.get(runtime, "python3"), *argv]


def _content_type_for(item: OutputFile) -> str:
    """产物 MIME:文本按 .md/其他区分,二进制按扩展名猜,兜底二进制流。"""
    if not item.binary:
        if item.path.lower().endswith(MARKDOWN_SUFFIXES):
            return "text/markdown"
        return "text/plain"
    guessed, _encoding = mimetypes.guess_type(item.path)
    return guessed or artifacts.DEFAULT_CONTENT_TYPE


def _unpack_failure_summary(result: SandboxExecResult) -> str:
    """解包失败时的可读原因(stdout 里的 JSON 优先,否则 stderr 尾巴)。"""
    try:
        payload = json.loads((result.stdout or "").strip() or "{}")
    except ValueError:
        payload = {}
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    tail = (result.stderr or "").strip()[-500:]
    return tail or f"解包脚本退出码 {result.exit_code}"
