"""沙箱运行时协议与数据契约(provider 无关)。

包含创建参数 `SandboxSpec`、执行结果 `ExecResult`、文件树条目 `FileEntry`,
以及业务层唯一依赖的两个 Protocol:`SandboxProvider` / `ShellSession`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024
DEFAULT_EXEC_TIMEOUT_SECONDS = 60
MAX_ERROR_DETAIL_CHARS = 300
# 持久 shell 会话:每次从流里读多少字节、默认终端尺寸
SHELL_READ_CHUNK = 65_536
DEFAULT_SHELL_COLS = 120
DEFAULT_SHELL_ROWS = 32
# 沙箱镜像里常见的交互式 shell;按顺序尝试,取第一个能起来的
_SHELL_CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/bin/sh", ("-i",)),
    ("/bin/bash", ("-i",)),
    ("sh", ("-i",)),
)


@dataclass(frozen=True)
class SandboxSpec:
    """创建沙箱所需的全部参数(provider 无关)。"""

    tenant_id: str
    name: str
    kind: str
    image: str
    cpus: float
    memory_mb: int
    network: str
    ttl_seconds: int


@dataclass(frozen=True)
class ExecResult:
    """一次命令执行的结果(stdout/stderr 已按上限截断)。"""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool


@dataclass(frozen=True)
class FileEntry:
    """文件树里的一个条目(扁平列表的一项)。

    - `path` 是**沙箱内**绝对路径(如 `/workspace/src/a.ts`);
    - `kind` 只取 file / dir / symlink / other;
    - `size` 是字节数,目录恒为 0(目录大小按文件系统语义没有可比性);
    - `modified` 是 mtime(epoch 秒),取不到时为 None。
    """

    path: str
    kind: str
    size: int
    modified: float | None = None


class ShellSession(Protocol):
    """一个持久交互式 shell(真 TTY,或退化后的管道)。

    - `read()` 返回下一段原始输出字节,EOF 返回 None;UTF-8 解码由上层做;
    - `write()` 把调用方输入**原样**写入(不补换行);
    - `close()` 幂等,尽力结束子进程/句柄;
    - `exit_code` 在进程退出后才有值,之前为 None;
    - `tty` 表示这个会话是否真的拿到了终端(local 退化管道时为 False)。
    """

    tty: bool
    exit_code: int | None

    async def read(self) -> bytes | None:
        """读下一段输出;EOF 返回 None(不抛异常)。"""
        ...

    async def write(self, data: bytes) -> None:
        """写输入;会话已结束时抛 `sandbox.session_closed`。"""
        ...

    async def close(self) -> None:
        """关闭会话并结束子进程(幂等)。"""
        ...


class SandboxProvider(Protocol):
    """沙箱运行时协议:创建/执行/文件读写/销毁,全部 async。"""

    provider: str

    async def create(self, spec: SandboxSpec) -> None:
        """按 spec 创建沙箱(已存在视为失败,由业务层生成唯一名)。"""
        ...

    async def exec(
        self,
        name: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        """执行命令;argv 不经过 shell,超时抛 `sandbox.exec_timeout`。"""
        ...

    async def write_file(self, name: str, path: str, data: bytes) -> None:
        """把二进制内容写到沙箱内 path。"""
        ...

    async def read_file(self, name: str, path: str) -> bytes:
        """读取沙箱内 path;不存在抛 `sandbox.file_not_found`。"""
        ...

    async def list_dir(
        self, name: str, path: str, *, depth: int = 1
    ) -> list[FileEntry]:
        """列目录:返回扁平列表(含各级嵌套项);depth=1 只列直接子项。

        符号链接只标记 `kind=symlink`,**不递归进入**(避免环与越界)。
        """
        ...

    async def destroy(self, name: str) -> None:
        """销毁沙箱;运行时已不存在时静默成功(幂等)。"""
        ...

    async def open_shell(
        self,
        name: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cols: int = DEFAULT_SHELL_COLS,
        rows: int = DEFAULT_SHELL_ROWS,
    ) -> ShellSession:
        """在沙箱内开一个持久交互式 shell;起不来抛 `sandbox.provider_error`。"""
        ...
