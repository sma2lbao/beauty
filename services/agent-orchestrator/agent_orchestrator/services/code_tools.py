"""编码 agent 的本地工作区 function tools(本地版)。

安全边界:
- 只允许配置的 roots(为空 = 不暴露任何代码工具);
- 所有路径 resolve 后必须仍在某个 root 内(防 ../ 与 symlink 逃逸);
- 写操作(风险等级 WRITE)在受控 roots 内**自动执行**(只有危险操作才进
  人工确认闸);敏感路径(.git/.env/node_modules 等)由写前的路径校验挡住;
- 不提供 shell,避免本地任意命令执行。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

MAX_READ_LINES = 400
MAX_FILE_BYTES = 512_000
MAX_RESULTS = 200
DENY_WRITE_PARTS = {".git", ".env", "node_modules", ".venv", ".uv-cache", ".nx"}
DENY_READ_PARTS = {
    ".git",
    ".env",
    "node_modules",
    ".venv",
    ".uv-cache",
    ".nx",
    ".cache",
}


def _error(message: str) -> str:
    import json

    return json.dumps({"error": message}, ensure_ascii=False)


def _roots(configured: list[str]) -> list[Path]:
    roots: list[Path] = []
    for item in configured:
        candidate = Path(item).expanduser().resolve()
        if candidate.is_dir():
            roots.append(candidate)
    return roots


def _resolve(roots: list[Path], raw: str) -> Path | None:
    try:
        candidate = Path(raw or ".").expanduser()
        if not candidate.is_absolute():
            candidate = roots[0] / candidate
        target = candidate.resolve()
    except OSError:
        return None
    for root in roots:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        return target
    return None


def _relative(root: Path, target: Path) -> str:
    try:
        return str(target.relative_to(root))
    except ValueError:
        return str(target)


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in DENY_READ_PARTS]
        for filename in filenames:
            yield Path(dirpath) / filename


def _int_arg(args: dict[str, Any], key: str, default: int, maximum: int) -> int:
    try:
        value = int(args.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def code_specs(configured_roots: list[str]):
    from agent_orchestrator.services.tools import ToolRisk, ToolSpec

    roots = _roots(configured_roots)
    if not roots:
        return []

    async def list_files(args: dict[str, Any]) -> str:
        raw = str(args.get("path") or ".")
        target = _resolve(roots, raw)
        if target is None:
            return _error("路径不在允许的 workspace root 内")
        if not target.exists():
            return _error(f"路径不存在:{raw}")
        pattern = str(args.get("pattern") or "*")
        limit = _int_arg(args, "limit", 100, MAX_RESULTS)
        root = next((r for r in roots if target.is_relative_to(r)), roots[0])
        if target.is_file():
            if any(part in DENY_READ_PARTS for part in target.parts):
                return _error("该路径禁止读取")
            files = [target]
        else:
            files = sorted(
                [
                    path
                    for path in target.rglob(pattern)
                    if path.is_file()
                    and not any(part in DENY_READ_PARTS for part in path.parts)
                ],
                key=lambda p: str(p),
            )[:limit]
        lines = [
            _relative(root, path)
            + ("/" if path.is_dir() else "")
            for path in files
        ]
        return "\n".join(lines) if lines else "(空)"

    async def read_file(args: dict[str, Any]) -> str:
        raw = str(args.get("path") or "")
        if not raw:
            return _error("缺少 path")
        target = _resolve(roots, raw)
        if target is None:
            return _error("路径不在允许的 workspace root 内")
        if any(part in DENY_READ_PARTS for part in target.parts):
            return _error("该路径禁止读取")
        if not target.is_file():
            return _error(f"文件不存在:{raw}")
        if target.stat().st_size > MAX_FILE_BYTES:
            return _error(f"文件超过 {MAX_FILE_BYTES} 字节上限")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _error("不是 UTF-8 文本文件")
        offset = _int_arg(args, "offset", 1, 100000)
        limit = _int_arg(args, "limit", MAX_READ_LINES, MAX_READ_LINES)
        lines = text.splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = [
            f"{offset + index:6d}\t{line}" for index, line in enumerate(selected)
        ]
        return "\n".join(numbered) if numbered else "(空文件或越界)"

    async def search_text(args: dict[str, Any]) -> str:
        import re

        pattern = str(args.get("pattern") or "")
        if not pattern:
            return _error("缺少 pattern")
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return _error(f"正则非法:{exc}")
        target = _resolve(roots, str(args.get("path") or "."))
        if target is None:
            return _error("路径不在允许的 workspace root 内")
        if not target.exists():
            return _error("路径不存在")
        root = next((r for r in roots if target.is_relative_to(r)), roots[0])
        matches: list[str] = []
        files = [target] if target.is_file() else list(_iter_files(target))
        for path in files:
            if any(part in DENY_READ_PARTS for part in path.parts):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        f"{_relative(root, path)}:{lineno}:{line[:300]}"
                    )
                    if len(matches) >= MAX_RESULTS:
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "(无匹配)"

    async def write_file(args: dict[str, Any]) -> str:
        raw = str(args.get("path") or "")
        content = str(args.get("content") or "")
        if not raw:
            return _error("缺少 path")
        if len(content.encode()) > MAX_FILE_BYTES:
            return _error(f"内容超过 {MAX_FILE_BYTES} 字节上限")
        target = _resolve(roots, raw)
        if target is None:
            return _error("路径不在允许的 workspace root 内")
        if any(part in DENY_WRITE_PARTS for part in target.parts):
            return _error("该路径禁止写入")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {raw}({len(content)} 字符)"

    async def edit_file(args: dict[str, Any]) -> str:
        raw = str(args.get("path") or "")
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        if not raw or not old:
            return _error("edit_file 需要 path/old_string")
        target = _resolve(roots, raw)
        if target is None:
            return _error("路径不在允许的 workspace root 内")
        if not target.is_file():
            return _error(f"文件不存在:{raw}")
        if any(part in DENY_WRITE_PARTS for part in target.parts):
            return _error("该路径禁止写入")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return _error("old_string 未找到")
        if count > 1:
            return _error("old_string 匹配多处,请提供更具体的文本")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"已编辑 {raw}"

    return [
        ToolSpec(
            name="code_list_files",
            description="列出 workspace 内文件(支持 glob pattern,只读)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string", "default": "*"},
                    "limit": {"type": "integer", "default": 100},
                },
            },
            handler=list_files,
        ),
        ToolSpec(
            name="code_read_file",
            description="读取 workspace 内 UTF-8 文本文件(带行号,只读)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": MAX_READ_LINES},
                },
                "required": ["path"],
            },
            handler=read_file,
        ),
        ToolSpec(
            name="code_search_text",
            description="在 workspace 内按正则搜索文本(只读)",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                },
                "required": ["pattern"],
            },
            handler=search_text,
        ),
        ToolSpec(
            name="code_write_file",
            description="写入 workspace 内文件(写入受控 workspace,自动执行)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
            risk=ToolRisk.WRITE,
        ),
        ToolSpec(
            name="code_edit_file",
            description="精确替换 workspace 内文件的一段文本(受控写入,自动执行)",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=edit_file,
            risk=ToolRisk.WRITE,
        ),
    ]
