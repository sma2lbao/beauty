"""沙箱运行时适配层:业务层只依赖 `SandboxProvider` 协议。

两种实现:

- `MicrosandboxProvider`:microsandbox microVM(默认,隔离);
- `LocalSandboxProvider`:宿主本地目录 + 子进程,**无隔离**,仅用于
  没有 microVM runtime 的本地开发/单租户可信环境。

microsandbox SDK 只在真正创建/执行时**惰性导入**——测试与没有 runtime 的
dev 启动不依赖它。所有 SDK 异常在这里统一收口为 `UpstreamError`,
不把堆栈/底层错误直接泄漏成 API 的 500。

除一次性 exec 外,协议还提供 **持久 shell 会话**(`open_shell`):microsandbox
用 `exec_stream(tty=True)` 拿真 TTY,local provider 用 POSIX pty(pty 不可用的
环境退化为管道,不是 TTY),输出/输入以字节流暴露给上层的会话管理器。

W5 拆包说明:实现按职责拆到同包子模块(`protocols` / `utils` /
`microsandbox` / `local` / `local_shell` / `unavailable` / `factory`),
本模块仍是唯一公开入口,重导出全部原有名字——外部
`from workspace.services.sandbox_provider import X` 的写法保持不变。
"""

from __future__ import annotations

from .factory import build_sandbox_provider
from .local import (
    LOCAL_HOME_SUBDIR,
    LOCAL_NAME_PATTERN,
    LOCAL_TMP_SUBDIR,
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
    _check_local_name,
    _local_entry_meta,
)
from .local_shell import (
    LocalPipeShellSession,
    LocalPtyShellSession,
    _pipe_shell_argv,
    _process_exit_code,
    _pty_child_setup,
    _set_winsize,
    _terminate_process,
)
from .microsandbox import MicrosandboxProvider, MicroShellSession
from .protocols import (
    _SHELL_CANDIDATES,
    DEFAULT_EXEC_TIMEOUT_SECONDS,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_SHELL_COLS,
    DEFAULT_SHELL_ROWS,
    MAX_ERROR_DETAIL_CHARS,
    SHELL_READ_CHUNK,
    ExecResult,
    FileEntry,
    SandboxProvider,
    SandboxSpec,
    ShellSession,
)
from .unavailable import UnavailableSandboxProvider
from .utils import (
    FS_ENTRY_KINDS,
    _child_path,
    _error_details,
    _fs_entry_kind,
    _fs_entry_modified,
    _fs_entry_path,
    _fs_entry_size,
    _import_sdk,
    _truncate_text,
)

__all__ = [
    # 常量
    "DEFAULT_EXEC_TIMEOUT_SECONDS",
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_SHELL_COLS",
    "DEFAULT_SHELL_ROWS",
    "FS_ENTRY_KINDS",
    "LOCAL_HOME_SUBDIR",
    "LOCAL_NAME_PATTERN",
    "LOCAL_TMP_SUBDIR",
    "LOCAL_WORKSPACE_SUBDIR",
    "MAX_ERROR_DETAIL_CHARS",
    "SHELL_READ_CHUNK",
    "_SHELL_CANDIDATES",
    # 数据契约 / 协议
    "ExecResult",
    "FileEntry",
    "SandboxProvider",
    "SandboxSpec",
    "ShellSession",
    # 实现
    "LocalPipeShellSession",
    "LocalPtyShellSession",
    "LocalSandboxProvider",
    "MicroShellSession",
    "MicrosandboxProvider",
    "UnavailableSandboxProvider",
    "build_sandbox_provider",
    # 模块级工具函数(拆分前就在本模块顶层,保持可导入)
    "_check_local_name",
    "_child_path",
    "_error_details",
    "_fs_entry_kind",
    "_fs_entry_modified",
    "_fs_entry_path",
    "_fs_entry_size",
    "_import_sdk",
    "_local_entry_meta",
    "_pipe_shell_argv",
    "_process_exit_code",
    "_pty_child_setup",
    "_set_winsize",
    "_terminate_process",
    "_truncate_text",
]
