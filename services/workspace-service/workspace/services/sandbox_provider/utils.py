"""provider 无关的小工具:SDK 惰性导入、错误细节、输出截断与路径/条目归一。"""

from __future__ import annotations

from pathlib import PurePosixPath
from types import ModuleType
from typing import Any

from common.errors import UpstreamError

from .protocols import MAX_ERROR_DETAIL_CHARS


def _import_sdk() -> ModuleType:
    """惰性导入 microsandbox:未安装时给出明确的 502 错误码。"""
    try:
        import microsandbox
    except ImportError as exc:  # pragma: no cover - 依赖已声明,仅防御性兜底
        raise UpstreamError(
            "sandbox.provider_unavailable",
            "microsandbox SDK 未安装,无法提供沙箱运行时",
        ) from exc
    return microsandbox


def _error_details(name: str, exc: BaseException) -> dict[str, Any]:
    """只暴露异常类型与短消息,不带 traceback。"""
    return {
        "sandbox_name": name,
        "error": f"{type(exc).__name__}: {str(exc)[:MAX_ERROR_DETAIL_CHARS]}",
    }


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """按 UTF-8 字节数截断文本,返回 (文本, 是否截断)。"""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore"), True


# microsandbox FsEntry.kind → 契约 kind;未知取值一律 other(不猜)
FS_ENTRY_KINDS = {
    "FILE": "file",
    "DIRECTORY": "dir",
    "SYMLINK": "symlink",
}


def _child_path(parent: str, name: str) -> str:
    """拼沙箱内绝对路径;parent 末尾有无 / 都归一(`/workspace` → /a)。"""
    return f"{parent.rstrip('/')}/{name}" if name else parent


def _fs_entry_path(entry: Any, parent: str) -> str:
    """推导条目在沙箱内的绝对路径,兼容 SDK 的 path 语义差异。

    microsandbox 的 `FsEntry` 只有 `path`(没有 `name`),而 Rust 实现没把
    `path` 语义写进文档:可能是绝对路径、相对列举目录的子路径,或只是名字。
    这里按形状归一——只取 basename 再拼会把 `/workspace/src/a.ts` 摊平成
    `/workspace/a.ts`,整棵子树的层级都会错。
    """
    raw = str(getattr(entry, "path", "") or "")
    if not raw or ".." in PurePosixPath(raw).parts:
        return ""
    candidate = raw if raw.startswith("/") else _child_path(parent, raw)
    normalized = PurePosixPath(candidate).as_posix()
    return normalized if normalized.startswith("/") else ""


def _fs_entry_kind(entry: Any) -> str:
    """FsEntry.kind 可能是字符串或枚举,统一取 .name 后查表。"""
    raw = getattr(entry, "kind", None)
    key = str(getattr(raw, "name", raw) or "").upper()
    return FS_ENTRY_KINDS.get(key, "other")


def _fs_entry_size(entry: Any) -> int:
    return max(0, int(getattr(entry, "size", 0) or 0))


def _fs_entry_modified(entry: Any) -> float | None:
    value = getattr(entry, "modified", None)
    return None if value is None else float(value)
