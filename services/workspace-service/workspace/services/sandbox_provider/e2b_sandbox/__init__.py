"""E2B 云沙箱实现:microVM 跑在 E2B 控制面,支持官方云与兼容 E2B 的厂商。

包化拆分(W5 出口):constants/helpers/session/provider;
本 __init__ 是门面,保持原导入路径与名字不变。

"""

from __future__ import annotations

from .constants import (
    _ENTRY_KINDS,
    _LIST_MAX_PAGES,
    _LIST_PAGE_SIZE,
    _META_ACCOUNT,
    _META_NAME,
)
from .helpers import (
    _entry_kind,
    _entry_modified,
    _entry_path,
    _entry_size,
    _import_e2b,
    _pty_size,
)
from .provider import (
    E2bSandboxProvider,
)
from .session import (
    E2bShellSession,
)

__all__ = [
    "_META_NAME",
    "_META_ACCOUNT",
    "_LIST_PAGE_SIZE",
    "_LIST_MAX_PAGES",
    "_ENTRY_KINDS",
    "_import_e2b",
    "_entry_path",
    "_entry_kind",
    "_entry_size",
    "_entry_modified",
    "_pty_size",
    "E2bShellSession",
    "E2bSandboxProvider",
    "asyncio",
    "os",
    "shlex",
    "time",
    "suppress",
    "datetime",
    "PurePosixPath",
    "ModuleType",
    "Any",
    "AppError",
    "NotFoundError",
    "UpstreamError",
    "DEFAULT_EXEC_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_SHELL_COLS",
    "DEFAULT_SHELL_ROWS",
    "ExecResult",
    "FileEntry",
    "SandboxSpec",
    "ShellSession",
    "_error_details",
    "_truncate_text",
]
