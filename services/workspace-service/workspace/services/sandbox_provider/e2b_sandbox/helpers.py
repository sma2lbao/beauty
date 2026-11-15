"""E2B SDK 惰性导入与条目/PTY 参数等小工具函数。"""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any

from common.errors import UpstreamError

from .constants import _ENTRY_KINDS


def _import_e2b() -> ModuleType:
    """惰性导入 e2b:未安装时给出明确的 502 错误码。"""
    try:
        import e2b
    except ImportError as exc:  # pragma: no cover - 依赖已声明,仅防御性兜底
        raise UpstreamError(
            "sandbox.provider_unavailable",
            "e2b SDK 未安装,无法提供 E2B 云沙箱运行时",
        ) from exc
    return e2b


def _entry_path(entry: Any, parent: str) -> str:
    """条目在沙箱内的绝对路径;SDK 偶尔只回相对路径,按列举目录归一。"""
    raw = str(getattr(entry, "path", "") or "")
    if not raw or ".." in PurePosixPath(raw).parts:
        return ""
    candidate = raw if raw.startswith("/") else f"{parent.rstrip('/')}/{raw}"
    normalized = PurePosixPath(candidate).as_posix()
    return normalized if normalized.startswith("/") else ""


def _entry_kind(entry: Any) -> str:
    """FileType 枚举/字符串 → 契约 kind;未知取值一律 other(不猜)。"""
    raw = getattr(entry, "type", None)
    key = str(getattr(raw, "value", raw) or "").lower()
    return _ENTRY_KINDS.get(key, "other")


def _entry_size(entry: Any) -> int:
    return max(0, int(getattr(entry, "size", 0) or 0))


def _entry_modified(entry: Any) -> float | None:
    value = getattr(entry, "modified_time", None)
    if isinstance(value, datetime):
        return value.timestamp()
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pty_size(sdk: ModuleType, cols: int, rows: int) -> Any:
    """PtySize 不在 e2b 顶层导出;测试假模块可直接提供 `PtySize`。"""
    size_cls = getattr(sdk, "PtySize", None)
    if size_cls is None:  # pragma: no cover - 真 SDK 的兜底路径
        from e2b.sandbox.commands.command_handle import PtySize as size_cls
    return size_cls(cols=cols, rows=rows)
