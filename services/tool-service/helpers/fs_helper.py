"""沙箱内文件工具 helper(read/glob/grep/list_dir/write/edit)。

由 tool-service 以 python3 -c <本文件源码> 启动:请求 JSON 经 stdin
传入(模型数据不进 argv),结果 JSON 打到 stdout。已处理的错误也返回
{ok:false,error:{...}} 且 exit 0;只有未预期异常才 exit 1。

约定:所有路径都是工作区(BM_WORKSPACE_ROOT,默认 /workspace)内的相对路径;
helper 自己再做一次 realpath + 拒绝名单校验,不信任上游校验结果(纵深防御)。
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

WORKSPACE_ROOT = os.path.realpath(os.environ.get("BM_WORKSPACE_ROOT") or "/workspace")
MAX_GREP_FILE_BYTES_FALLBACK = 2 * 1024 * 1024
MAX_TEXT_BYTES_FALLBACK = 8 * 1024 * 1024


class ToolFailure(Exception):
    """可预期的工具错误:转成错误信封,不打印堆栈。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def error_envelope(code: str, message: str, hint: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
            "retryable": False,
        },
    }


# ---- 路径与遍历 ---------------------------------------------------------


def _deny_parts(policy: dict[str, Any], *, for_write: bool) -> set[str]:
    key = "deny_write_parts" if for_write else "deny_read_parts"
    return {str(item).casefold() for item in policy.get(key) or ()}


def _raw_parts(raw: str) -> list[str]:
    return [part for part in raw.replace("\\", "/").split("/") if part]


def resolve_path(
    raw: Any, policy: dict[str, Any], *, for_write: bool = False
) -> tuple[str, str, list[str]]:
    """校验相对路径并解析为 (绝对路径, 相对路径, 路径段)。

    顺序:类型/长度 → 绝对路径与 .. 拒绝 → realpath → 前缀校验 → 拒绝名单。
    """

    if not isinstance(raw, str) or not raw.strip():
        raise ToolFailure("tool.invalid_args", "path 必须是非空字符串")
    if "\x00" in raw:
        raise ToolFailure("tool.invalid_path", "path 不能包含 NUL 字符")
    if any(ord(char) < 32 for char in raw):
        raise ToolFailure("tool.invalid_path", "path 不能包含控制字符")
    max_bytes = int(policy.get("max_path_bytes") or 512)
    if len(raw.encode("utf-8")) > max_bytes:
        raise ToolFailure(
            "tool.path_too_long", "path 超过长度上限", f"上限 {max_bytes} 字节"
        )
    if raw.startswith("/") or raw.startswith("~"):
        raise ToolFailure(
            "tool.invalid_path",
            "path 必须是工作区内的相对路径",
            "不要以 / 或 ~ 开头",
        )
    raw_parts = _raw_parts(raw)
    if ".." in raw_parts:
        raise ToolFailure(
            "tool.path_escaped", "path 不能包含 ..", "工作区外的路径一律拒绝"
        )
    if for_write:
        _reject_symlink_components(raw_parts)
    target = os.path.realpath(os.path.join(WORKSPACE_ROOT, raw))
    if target != WORKSPACE_ROOT and not target.startswith(WORKSPACE_ROOT + os.sep):
        raise ToolFailure("tool.path_escaped", "path 解析后位于工作区之外")
    rel = os.path.relpath(target, WORKSPACE_ROOT)
    parts = [] if rel == "." else rel.split(os.sep)
    deny = _deny_parts(policy, for_write=for_write)
    for part in parts:
        if part.casefold() in deny:
            raise ToolFailure("tool.denied", f"路径包含被拒绝的片段: {part}")
    if for_write:
        name = parts[-1].casefold() if parts else ""
        if name.startswith(".env"):
            raise ToolFailure("tool.denied", "禁止写入 .env 系列文件")
        for suffix in policy.get("deny_write_suffixes") or ():
            if name.endswith(str(suffix).casefold()):
                raise ToolFailure("tool.denied", f"禁止写入敏感后缀文件: {suffix}")
    return target, rel, parts


def _reject_symlink_components(parts: list[str]) -> None:
    """写路径逐段 lstat:任何一段是符号链接都拒绝(防链接重定向写)。"""

    current = WORKSPACE_ROOT
    for part in parts:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ToolFailure(
                "tool.io_error", "检查写入路径失败", str(exc)[:200]
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise ToolFailure(
                "tool.symlink_denied",
                "写入路径包含符号链接,已拒绝",
                "请让用户改用真实文件路径",
            )


def _is_pruned(parts: list[str], policy: dict[str, Any]) -> bool:
    """遍历剪枝:构建产物目录 + 只读拒绝名单都不进 glob/grep 遍历。

    resolve_path 只拦截直接指向拒绝片段的路径;遍历时若不剪枝,
    .git/node_modules 里的文件名与内容仍会经 glob/grep 泄漏。
    """

    prune = {str(item).casefold() for item in policy.get("prune_dirs") or ()}
    prune |= _deny_parts(policy, for_write=False)
    return any(part.casefold() in prune for part in parts)


def _read_bytes(target: str, *, limit: int, code: str = "tool.too_large") -> bytes:
    try:
        size = os.path.getsize(target)
    except OSError as exc:
        raise ToolFailure("tool.not_found", "文件不存在或不可读") from exc
    if size > limit:
        raise ToolFailure(
            code,
            "文件超过大小上限",
            f"上限 {limit} 字节,当前 {size} 字节;请改用 grep/glob 或分段读取",
        )
    try:
        with open(target, "rb") as handle:
            return handle.read()
    except PermissionError as exc:
        raise ToolFailure("tool.permission_denied", "没有权限读取该文件") from exc
    except OSError as exc:
        raise ToolFailure("tool.io_error", "读取文件失败") from exc


def _decode_text(data: bytes) -> tuple[str, bool]:
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        raise ToolFailure(
            "tool.unsupported_encoding",
            "不支持 UTF-16 文本",
            "请让用户提供 UTF-8 编码的文件",
        )
    if b"\x00" in data:
        raise ToolFailure("tool.binary_file", "文件包含二进制内容(检测到 NUL 字节)")
    bom = data.startswith(b"\xef\xbb\xbf")
    payload = data[3:] if bom else data
    try:
        return payload.decode("utf-8"), bom
    except UnicodeDecodeError as exc:
        raise ToolFailure(
            "tool.unsupported_encoding",
            "文件不是 UTF-8 文本",
            "仅支持 UTF-8(可带 BOM)",
        ) from exc


def _display_path(parts: list[str]) -> str:
    return "/".join(parts)


# ---- read ---------------------------------------------------------------


def tool_read(arguments: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    target, rel, parts = resolve_path(arguments.get("path"), policy)
    if os.path.isdir(target):
        raise ToolFailure(
            "tool.is_directory", "path 指向目录", "读取目录请改用 list_dir"
        )
    if not os.path.exists(target):
        raise ToolFailure("tool.not_found", "文件不存在")
    limit = int(arguments.get("limit") or policy.get("default_read_lines") or 200)
    max_lines = int(policy.get("max_read_lines") or 2000)
    if limit < 1 or limit > max_lines:
        raise ToolFailure("tool.invalid_args", f"limit 必须在 1..{max_lines} 之间")
    offset = int(arguments.get("offset") or 1)
    if offset < 1:
        raise ToolFailure("tool.invalid_args", "offset 从 1 开始")
    max_line_chars = int(
        arguments.get("max_line_chars") or policy.get("default_line_chars") or 2000
    )
    hard_line_chars = int(policy.get("max_line_chars") or 4000)
    if max_line_chars < 80 or max_line_chars > hard_line_chars:
        raise ToolFailure(
            "tool.invalid_args",
            f"max_line_chars 必须在 80..{hard_line_chars} 之间",
        )
    data = _read_bytes(
        target,
        limit=int(policy.get("max_read_file_bytes") or MAX_TEXT_BYTES_FALLBACK),
    )
    text, bom = _decode_text(data)
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    total_lines = len(lines)
    selected = lines[offset - 1 : offset - 1 + limit]
    char_budget = int(policy.get("max_read_chars") or 25_000)
    rendered: list[str] = []
    used = 0
    for index, line in enumerate(selected):
        if len(line) > max_line_chars:
            line = line[:max_line_chars] + "\u2026"
        row = f"{offset + index:6d}\t{line}"
        if rendered and used + len(row) + 1 > char_budget:
            break
        rendered.append(row)
        used += len(row) + 1
    returned_lines = len(rendered)
    truncated = offset - 1 + returned_lines < total_lines
    notes: list[str] = []
    if offset > total_lines:
        notes.append("offset 超出文件行数")
    if truncated:
        notes.append("结果已截断;用 offset 继续读取")
    return {
        "ok": True,
        "path": _display_path(parts) or ".",
        "total_lines": total_lines,
        "offset": offset,
        "limit": limit,
        "returned_lines": returned_lines,
        "truncated": truncated,
        "next_offset": offset + returned_lines if truncated else None,
        "bytes": len(data),
        "encoding": "utf-8",
        "bom": bom,
        "ends_with_newline": ends_with_newline,
        "sha256": hashlib.sha256(data).hexdigest(),
        "content": "\n".join(rendered),
        "notes": notes,
    }


# ---- glob ---------------------------------------------------------------


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern[index : index + 2] == "**":
                index += 2
                if pattern[index : index + 1] == "/":
                    out.append("(?:.*/)?")
                    index += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def tool_glob(arguments: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolFailure("tool.invalid_args", "pattern 必须是非空字符串")
    if not any(mark in pattern for mark in ("*", "?", "[")):
        raise ToolFailure(
            "tool.invalid_args",
            "glob 的 pattern 必须包含通配符",
            "列出固定目录请用 list_dir",
        )
    base, rel, _parts = resolve_path(arguments.get("path") or ".", policy)
    if not os.path.exists(base):
        raise ToolFailure("tool.not_found", "path 不存在")
    if not os.path.isdir(base):
        raise ToolFailure("tool.not_directory", "glob 的 path 必须是目录")
    case_sensitive = bool(arguments.get("case_sensitive", False))
    include_dirs = bool(arguments.get("include_dirs", False))
    max_results = int(
        arguments.get("max_results") or policy.get("default_glob_results") or 200
    )
    hard_max = int(policy.get("max_glob_results") or 1000)
    if max_results < 1 or max_results > hard_max:
        raise ToolFailure("tool.invalid_args", f"max_results 必须在 1..{hard_max} 之间")
    regex = _glob_to_regex(pattern)
    compiled = regex if case_sensitive else re.compile(regex.pattern, re.IGNORECASE)
    max_depth = int(policy.get("max_glob_depth") or 32)
    char_budget = int(policy.get("max_glob_chars") or 10_000)
    entries: list[str] = []
    used = 0
    scanned_dirs = 0
    truncated = False
    for current, dirs, files in os.walk(base, topdown=True, followlinks=False):
        current_rel = os.path.relpath(current, base)
        depth = 0 if current_rel == "." else len(current_rel.split(os.sep))
        scanned_dirs += 1
        current_parts = (
            [] if current_rel == "." else _raw_parts(current_rel.replace(os.sep, "/"))
        )
        dirs[:] = sorted(
            name
            for name in dirs
            if not os.path.islink(os.path.join(current, name))
            and not _is_pruned(current_parts + [name], policy)
            and depth + 1 <= max_depth
        )
        for name in list(files) + list(dirs):
            full = os.path.join(current, name)
            if os.path.islink(full):
                continue
            if not include_dirs and name in dirs:
                continue
            candidate_rel = (
                name if current_rel == "." else os.path.join(current_rel, name)
            )
            display = candidate_rel.replace(os.sep, "/")
            if not compiled.match(display):
                continue
            if used + len(display) + 1 > char_budget or len(entries) >= max_results:
                truncated = True
                break
            entries.append(display)
            used += len(display) + 1
        if truncated:
            break
    return {
        "ok": True,
        "entries": entries,
        "shown": len(entries),
        "truncated": truncated,
        "scanned_dirs": scanned_dirs,
        "notes": ["结果已截断;请缩小 pattern 或提高 max_results"] if truncated else [],
    }


# ---- grep ---------------------------------------------------------------


def _grep_targets(
    base: str,
    *,
    max_depth: int,
    policy: dict[str, Any],
    deadline: float,
) -> list[str]:
    if os.path.isfile(base):
        return [base]
    files: list[str] = []
    for current, dirs, names in os.walk(base, topdown=True, followlinks=False):
        if time.monotonic() > deadline:
            break
        current_rel = os.path.relpath(current, base)
        depth = 0 if current_rel == "." else len(current_rel.split(os.sep))
        current_parts = (
            [] if current_rel == "." else _raw_parts(current_rel.replace(os.sep, "/"))
        )
        dirs[:] = sorted(
            name
            for name in dirs
            if not os.path.islink(os.path.join(current, name))
            and depth + 1 <= max_depth
            and not _is_pruned(current_parts + [name], policy)
        )
        for name in sorted(names):
            full = os.path.join(current, name)
            if os.path.islink(full):
                continue
            files.append(full)
    return files


def tool_grep(arguments: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ToolFailure("tool.invalid_args", "pattern 必须是非空字符串")
    max_pattern = int(policy.get("max_grep_pattern_chars") or 1000)
    if len(pattern) > max_pattern:
        raise ToolFailure("tool.pattern_too_long", f"pattern 超过 {max_pattern} 个字符")
    flags = 0
    if arguments.get("multiline"):
        flags |= re.MULTILINE | re.DOTALL
    needle = re.escape(pattern) if arguments.get("fixed_strings") else pattern
    if not arguments.get("case_sensitive", False):
        flags |= re.IGNORECASE
    try:
        compiled = re.compile(needle, flags)
    except re.error as exc:
        raise ToolFailure("tool.bad_pattern", f"正则表达式无效: {exc.msg}") from exc
    base, rel, _parts = resolve_path(arguments.get("path") or ".", policy)
    if not os.path.exists(base):
        raise ToolFailure("tool.not_found", "path 不存在")
    glob_filter = arguments.get("glob")
    max_results = int(
        arguments.get("max_results") or policy.get("default_grep_results") or 100
    )
    hard_max = int(policy.get("max_grep_results") or 500)
    if max_results < 1 or max_results > hard_max:
        raise ToolFailure("tool.invalid_args", f"max_results 必须在 1..{hard_max} 之间")
    context_lines = int(arguments.get("context_lines") or 0)
    max_context = int(policy.get("max_context_lines") or 3)
    if context_lines < 0 or context_lines > max_context:
        raise ToolFailure("tool.invalid_args", f"context_lines 必须在 0..{max_context}")
    deadline = time.monotonic() + float(policy.get("grep_deadline_seconds") or 10.0)
    max_files = int(policy.get("max_grep_files") or 5000)
    max_file_bytes = int(
        policy.get("max_grep_file_bytes") or MAX_GREP_FILE_BYTES_FALLBACK
    )
    char_budget = int(policy.get("max_grep_chars") or 25_000)
    line_scan_chars = int(policy.get("max_line_scan_chars") or 20_000)
    skipped = {"too_large": 0, "binary": 0, "denied": 0}
    matches: list[dict[str, Any]] = []
    used = 0
    truncated = False
    files_scanned = 0
    targets = _grep_targets(
        base,
        max_depth=int(policy.get("max_glob_depth") or 32),
        policy=policy,
        deadline=deadline,
    )
    base_rel = "" if rel == "." else rel.replace(os.sep, "/")
    for target in targets:
        if files_scanned >= max_files or time.monotonic() > deadline:
            truncated = True
            break
        try:
            size = os.path.getsize(target)
        except OSError:
            continue
        if target == base:
            display = base_rel.replace(os.sep, "/")
        else:
            relative = os.path.relpath(target, base).replace(os.sep, "/")
            display = f"{base_rel}/{relative}" if base_rel else relative
        if glob_filter and not (
            fnmatch.fnmatch(display, str(glob_filter))
            or fnmatch.fnmatch(os.path.basename(display), str(glob_filter))
        ):
            continue
        if size > max_file_bytes:
            skipped["too_large"] += 1
            continue
        try:
            with open(target, "rb") as handle:
                data = handle.read()
        except PermissionError:
            skipped["denied"] += 1
            continue
        except OSError:
            continue
        if b"\x00" in data:
            skipped["binary"] += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped["binary"] += 1
            continue
        files_scanned += 1
        lines = text.split("\n")
        if arguments.get("multiline"):
            for found in compiled.finditer(text):
                line_no = text.count("\n", 0, found.start()) + 1
                line_start = text.rfind("\n", 0, found.start()) + 1
                item = _grep_item(
                    lines,
                    display,
                    line_no,
                    found.start() - line_start + 1,
                    context_lines,
                    line_scan_chars,
                )
                if used + len(json.dumps(item, ensure_ascii=False)) > char_budget:
                    truncated = True
                    break
                matches.append(item)
                used += len(json.dumps(item, ensure_ascii=False))
                if len(matches) >= max_results:
                    truncated = True
                    break
        else:
            for number, line in enumerate(lines, start=1):
                found = compiled.search(line[:line_scan_chars])
                if found is None:
                    continue
                item = _grep_item(
                    lines,
                    display,
                    number,
                    found.start() + 1,
                    context_lines,
                    line_scan_chars,
                )
                if used + len(json.dumps(item, ensure_ascii=False)) > char_budget:
                    truncated = True
                    break
                matches.append(item)
                used += len(json.dumps(item, ensure_ascii=False))
                if len(matches) >= max_results:
                    truncated = True
                    break
        if truncated:
            break
    return {
        "ok": True,
        "matches": matches,
        "shown": len(matches),
        "truncated": truncated,
        "files_scanned": files_scanned,
        "files_skipped": skipped,
        "elapsed_ms": 0,
        "notes": ["结果已截断;请缩小 pattern/path 或降低 max_results"]
        if truncated
        else [],
    }


def _grep_item(
    lines: list[str],
    display: str,
    line_no: int,
    column: int,
    context: int,
    line_scan_chars: int,
) -> dict[str, Any]:
    text = lines[line_no - 1][:line_scan_chars]
    before: list[str] = []
    after: list[str] = []
    if context:
        low = max(1, line_no - context)
        high = min(len(lines), line_no + context)
        before = [row[:line_scan_chars] for row in lines[low - 1 : line_no - 1]]
        after = [row[:line_scan_chars] for row in lines[line_no:high]]
    return {
        "path": display,
        "line": line_no,
        "column": column,
        "text": text,
        "context_before": before,
        "context_after": after,
    }


# ---- list_dir -----------------------------------------------------------


def tool_list_dir(arguments: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    base, rel, parts = resolve_path(arguments.get("path") or ".", policy)
    if not os.path.exists(base):
        raise ToolFailure("tool.not_found", "path 不存在")
    if not os.path.isdir(base):
        raise ToolFailure("tool.not_directory", "path 必须是目录")
    depth = int(arguments.get("depth") or 1)
    hard_depth = int(policy.get("max_list_depth") or 3)
    if depth < 1 or depth > hard_depth:
        raise ToolFailure("tool.invalid_args", f"depth 必须在 1..{hard_depth} 之间")
    include_hidden = bool(arguments.get("include_hidden", False))
    max_entries = int(
        arguments.get("max_entries") or policy.get("default_list_entries") or 200
    )
    hard_max = int(policy.get("max_list_entries") or 1000)
    if max_entries < 1 or max_entries > hard_max:
        raise ToolFailure("tool.invalid_args", f"max_entries 必须在 1..{hard_max} 之间")
    deny_read = _deny_parts(policy, for_write=False)
    entries: list[dict[str, Any]] = []
    truncated = False
    pending: list[tuple[str, int]] = [(base, 0)]
    while pending:
        current, level = pending.pop(0)
        try:
            names = sorted(os.listdir(current))
        except OSError:
            continue
        current_rel = os.path.relpath(current, base)
        for name in names:
            if name in deny_read:
                continue
            if not include_hidden and name.startswith("."):
                continue
            full = os.path.join(current, name)
            if os.path.islink(full):
                continue
            display = (
                name
                if current_rel == "."
                else f"{current_rel.replace(os.sep, '/')}/{name}"
            )
            is_dir = os.path.isdir(full)
            try:
                stat_result = os.stat(full)
            except OSError:
                continue
            entries.append(
                {
                    "name": display,
                    "type": "dir" if is_dir else "file",
                    "size": 0 if is_dir else int(stat_result.st_size),
                    "modified_at": _iso(stat_result.st_mtime),
                }
            )
            if is_dir and level + 1 < depth and not _is_pruned(parts + [name], policy):
                pending.append((full, level + 1))
            if len(entries) >= max_entries:
                truncated = True
                pending.clear()
                break
    entries.sort(key=lambda item: (item["type"] != "dir", item["name"]))
    return {
        "ok": True,
        "path": _display_path(parts) or ".",
        "entries": entries,
        "shown": len(entries),
        "truncated": truncated,
        "notes": ["结果已截断;请提高 max_entries 或缩小 depth"] if truncated else [],
    }


# ---- write / edit -------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(target: str) -> str:
    """流式哈希目标文件(避免把大文件整块读进内存)。"""

    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _reject_unsafe_target(target: str, rel: str, policy: dict[str, Any]) -> None:
    """写目标最后一段拒绝 symlink / 硬链接(纵深防御,不依赖 resolve_path)。"""

    raw_target = os.path.join(WORKSPACE_ROOT, rel)
    if os.path.islink(raw_target):
        raise ToolFailure(
            "tool.symlink_denied",
            "目标文件是符号链接,禁止写入",
            "请让用户改用真实路径或删除链接",
        )
    if os.path.exists(target):
        try:
            stat_result = os.stat(target)
        except OSError as exc:
            raise ToolFailure("tool.io_error", "无法读取目标文件状态") from exc
        if stat_result.st_nlink > 1:
            raise ToolFailure(
                "tool.hardlink_denied",
                "目标文件有多个硬链接,禁止写入",
                "请让用户先解除硬链接",
            )


def _check_expected(
    before: str | None, arguments: dict[str, Any], *, policy: dict[str, Any]
) -> None:
    """乐观锁与读前置校验:expected_sha256 / _expected_read_sha256。"""

    expected = arguments.get("expected_sha256")
    if isinstance(expected, str) and expected:
        if before is None:
            raise ToolFailure(
                "tool.conflict",
                "目标文件不存在,与 expected_sha256 不符",
                "创建新文件时不要带 expected_sha256",
            )
        if before != expected:
            raise ToolFailure(
                "tool.conflict",
                "文件内容已变化,expected_sha256 不匹配",
                f"当前 sha256={before};请重新 read 后再重试",
            )
    read_sha = arguments.get("_expected_read_sha256")
    stale = before is None or before != read_sha
    if isinstance(read_sha, str) and read_sha and stale:
        raise ToolFailure(
            "tool.stale_read",
            "文件内容在读取后已变化,请重新 read 后再写",
            f"当前 sha256={before or 'missing'}",
        )


def _atomic_write(target: str, payload: bytes, *, existing: bool) -> None:
    """同目录随机临时文件 + fsync + os.replace,保证不出现半截文件。"""

    parent = os.path.dirname(target)
    mode = 0o644
    if existing:
        try:
            mode = os.stat(target).st_mode & 0o777
        except OSError:
            mode = 0o644
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=parent, prefix=".bm-write-", delete=False
        ) as handle:
            temporary = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except OSError as exc:
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
        raise ToolFailure("tool.io_error", "写入文件失败", str(exc)[:200]) from exc
    try:
        dir_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def tool_write(arguments: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    content = arguments.get("content")
    if not isinstance(content, str):
        raise ToolFailure("tool.invalid_args", "content 必须是字符串")
    payload = content.encode("utf-8")
    max_bytes = int(policy.get("max_write_bytes") or 512_000)
    if len(payload) > max_bytes:
        raise ToolFailure(
            "tool.too_large",
            "写入内容超过单次上限",
            f"上限 {max_bytes} 字节,当前 {len(payload)} 字节;请拆分写入",
        )
    target, rel, parts = resolve_path(arguments.get("path"), policy, for_write=True)
    _reject_unsafe_target(target, rel, policy)
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        if not bool(arguments.get("create_parents", True)):
            raise ToolFailure(
                "tool.no_parent",
                "父目录不存在且 create_parents=false",
                "先创建目录,或设 create_parents=true",
            )
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise ToolFailure(
                "tool.io_error", "创建父目录失败", str(exc)[:200]
            ) from exc
        recheck, _, _ = resolve_path(arguments.get("path"), policy, for_write=True)
        if recheck != target:
            raise ToolFailure("tool.path_escaped", "父目录创建后路径解析不一致")
    exists = os.path.exists(target)
    if exists and os.path.isdir(target):
        raise ToolFailure("tool.is_directory", "path 指向目录")
    if exists and not bool(arguments.get("overwrite", True)):
        raise ToolFailure("tool.exists", "目标文件已存在且 overwrite=false")
    before = _hash_file(target) if exists else None
    _check_expected(before, arguments, policy=policy)
    after = _sha256(payload)
    display = _display_path(parts)
    notes: list[str] = []
    if exists and before == after:
        notes.append("内容与现有文件相同,未写入")
        return {
            "ok": True,
            "path": display,
            "created": False,
            "changed": False,
            "bytes": len(payload),
            "lines": _count_lines(content),
            "sha256_before": before,
            "sha256_after": after,
            "notes": notes,
        }
    _atomic_write(target, payload, existing=exists)
    return {
        "ok": True,
        "path": display,
        "created": not exists,
        "changed": True,
        "bytes": len(payload),
        "lines": _count_lines(content),
        "sha256_before": before,
        "sha256_after": after,
        "notes": notes,
    }


def _near_miss(text: str, old_string: str) -> str:
    """零匹配时给模型可操作提示:换行风格/空白/上下文变化。"""

    hints: list[str] = []
    stripped = old_string.strip()
    if "\r\n" in text and "\r\n" not in old_string:
        hints.append("文件使用 CRLF 换行,old_string 里是 LF")
    if stripped and stripped != old_string and stripped in text:
        hints.append("old_string 首尾空白与文件不一致")
    first_line = next((line for line in old_string.splitlines() if line.strip()), "")
    if first_line and first_line not in text:
        hints.append("old_string 的首行在文件中不存在,上下文可能已变化")
    return "; ".join(hints) or "请重新 read 目标文件后再构造 old_string"


def tool_edit(arguments: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    if not isinstance(old_string, str) or not old_string:
        raise ToolFailure("tool.invalid_args", "old_string 必须是非空字符串")
    if not isinstance(new_string, str):
        raise ToolFailure("tool.invalid_args", "new_string 必须是字符串")
    payload = new_string.encode("utf-8")
    max_bytes = int(policy.get("max_write_bytes") or 512_000)
    if len(payload) > max_bytes:
        raise ToolFailure(
            "tool.too_large",
            "new_string 超过单次上限",
            f"上限 {max_bytes} 字节",
        )
    target, rel, parts = resolve_path(arguments.get("path"), policy, for_write=True)
    _reject_unsafe_target(target, rel, policy)
    if not os.path.exists(target):
        raise ToolFailure("tool.not_found", "目标文件不存在")
    if os.path.isdir(target):
        raise ToolFailure("tool.is_directory", "path 指向目录")
    data = _read_bytes(
        target,
        limit=int(policy.get("max_edit_file_bytes") or 2 * 1024 * 1024),
    )
    text, bom = _decode_text(data)
    before = _sha256(data)
    _check_expected(before, arguments, policy=policy)
    if old_string == new_string:
        raise ToolFailure("tool.no_change", "old_string 与 new_string 相同,未做替换")
    count = text.count(old_string)
    if count == 0:
        raise ToolFailure(
            "tool.edit_no_match",
            "old_string 在文件中未找到",
            _near_miss(text, old_string),
        )
    replace_all = bool(arguments.get("replace_all", False))
    if count > 1 and not replace_all:
        line_numbers = [
            number
            for number, line in enumerate(text.split("\n"), start=1)
            if old_string in line
        ][:5]
        raise ToolFailure(
            "tool.edit_ambiguous",
            f"old_string 匹配 {count} 处,需要唯一匹配",
            f"命中行号示例: {line_numbers};多处替换请设 replace_all=true",
        )
    updated = (
        text.replace(old_string, new_string)
        if replace_all
        else text.replace(old_string, new_string, 1)
    )
    new_payload = (b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8")
    if len(new_payload) > max_bytes:
        raise ToolFailure(
            "tool.too_large",
            "替换后文件超过单次上限",
            f"上限 {max_bytes} 字节",
        )
    after = _sha256(new_payload)
    _atomic_write(target, new_payload, existing=True)
    return {
        "ok": True,
        "path": _display_path(parts),
        "replacements": count if replace_all else 1,
        "sha256_before": before,
        "sha256_after": after,
        "bytes": len(new_payload),
        "notes": [],
    }


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


HANDLERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "read": tool_read,
    "glob": tool_glob,
    "grep": tool_grep,
    "list_dir": tool_list_dir,
    "write": tool_write,
    "edit": tool_edit,
}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        print(json.dumps(error_envelope("tool.invalid_args", "stdin 不是合法 JSON")))
        return 0
    if not isinstance(request, dict):
        print(json.dumps(error_envelope("tool.invalid_args", "stdin 必须是 JSON 对象")))
        return 0
    tool = request.get("tool")
    handler = HANDLERS.get(str(tool))
    if handler is None:
        print(
            json.dumps(
                error_envelope("tool.unknown_tool", f"沙箱 helper 不认识工具: {tool}")
            )
        )
        return 0
    arguments = request.get("arguments") or {}
    policy = request.get("policy") or {}
    if not isinstance(arguments, dict) or not isinstance(policy, dict):
        print(
            json.dumps(
                error_envelope("tool.invalid_args", "arguments/policy 必须是 JSON 对象")
            )
        )
        return 0
    try:
        result = handler(arguments, policy)
    except ToolFailure as failure:
        print(
            json.dumps(
                error_envelope(failure.code, failure.message, failure.hint),
                ensure_ascii=False,
            )
        )
        return 0
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - 未预期异常:stderr 给上游,exit 1
        print(f"fs_helper failed: {exc}"[:300], file=sys.stderr)
        sys.exit(1)
