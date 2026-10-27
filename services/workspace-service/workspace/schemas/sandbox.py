"""沙箱契约:创建/读取/执行/文件读写/幂等 ensure。

上限在这里做**请求侧**硬校验;服务层还会按配置(sandbox_max_ttl_seconds 等)
再收紧一次,配置可以比这里的硬上限更保守。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from common.errors import AppError
from pydantic import BaseModel, ConfigDict, Field, field_validator

WORKSPACE_ROOT = "/workspace"
DEFAULT_CWD = WORKSPACE_ROOT
MAX_TTL_SECONDS = 7200
MAX_CPUS = 4.0
MAX_MEMORY_MB = 4096
MAX_ARGV = 64
MAX_EXEC_TIMEOUT_SECONDS = 600
MAX_PATH_LENGTH = 512
MAX_ENV_ITEMS = 64
MAX_STDIN_CHARS = 1_000_000
MAX_BASE64_CHARS = 16_000_000
# 文件树列举:深度与条目数硬上限(服务层按这两个值再截断一次)
MAX_TREE_DEPTH = 4
MAX_TREE_ENTRIES = 500
# ensure 幂等键(设计 §11.2):conv:<conversation_id> / run:<run_id>,最长 96
MAX_SCOPE_KEY_LENGTH = 96
SCOPE_KEY_PATTERN = r"^[a-z0-9:_-]+$"

SandboxKind = Literal["skill", "mcp", "generic"]
SandboxNetwork = Literal["deny", "public"]


class SandboxCreate(BaseModel):
    """创建沙箱请求;不传 image/ttl/资源时用服务配置的默认值。"""

    kind: SandboxKind = "generic"
    image: str | None = Field(default=None, min_length=1, max_length=120)
    ttl_seconds: int | None = Field(default=None, ge=1, le=MAX_TTL_SECONDS)
    # 不传则用服务配置的 sandbox_default_*(配置可整体调整,这里只做硬上限)
    cpus: float | None = Field(default=None, gt=0, le=MAX_CPUS)
    memory_mb: int | None = Field(default=None, ge=64, le=MAX_MEMORY_MB)
    network: SandboxNetwork | None = None
    workspace_id: str | None = Field(default=None, max_length=32)
    run_id: str | None = Field(default=None, max_length=32)


class SandboxEnsure(SandboxCreate):
    """幂等 ensure 请求:scope_key 是租户内幂等键(P0-05)。

    tool-service 每次工具调用都带同一个 scope_key(conv:<id> / run:<id>),
    服务端按 (tenant_id, scope_key) get-or-create,多副本并发也只保留一行。
    """

    scope_key: str = Field(
        min_length=1,
        max_length=MAX_SCOPE_KEY_LENGTH,
        pattern=SCOPE_KEY_PATTERN,
    )


class SandboxRead(BaseModel):
    """沙箱实例(生命周期与配额元数据)。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    provider: str
    image: str
    cpus: float
    memory_mb: int
    network: str
    workspace_id: str | None
    run_id: str | None
    scope_key: str | None = None
    status: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    last_error: str | None


class SandboxEnsureRead(BaseModel):
    """ensure 响应:sandbox 是复用的实例,created 表示本次是否新建了运行时。"""

    sandbox: SandboxRead
    created: bool


class SandboxExecRequest(BaseModel):
    """在沙箱里执行一条命令(argv[0] 为可执行文件,不经过 shell)。"""

    argv: list[str] = Field(min_length=1, max_length=MAX_ARGV)
    cwd: str = Field(default=DEFAULT_CWD, max_length=MAX_PATH_LENGTH)
    env: dict[str, str] = Field(default_factory=dict, max_length=MAX_ENV_ITEMS)
    stdin: str | None = Field(default=None, max_length=MAX_STDIN_CHARS)
    # 服务端再与 sandbox_exec_timeout_seconds 取较小值,防止单请求长时间占用
    timeout_seconds: int | None = Field(default=None, ge=1, le=MAX_EXEC_TIMEOUT_SECONDS)

    @field_validator("argv")
    @classmethod
    def _check_argv(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("argv 不能包含空字符串")
        return value

    @field_validator("cwd")
    @classmethod
    def _check_cwd(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value.split("/"):
            raise ValueError("cwd 必须是以 / 开头的绝对路径,且不能包含 ..")
        return value


class SandboxExecRead(BaseModel):
    """命令执行结果;输出超限时 truncated=true。"""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool


class SandboxFileWrite(BaseModel):
    """写文件请求:内容用 Base64,避免 JSON 里出现二进制/换行问题。"""

    path: str = Field(min_length=1, max_length=MAX_PATH_LENGTH)
    content_base64: str = Field(max_length=MAX_BASE64_CHARS)


class SandboxFileRead(BaseModel):
    """读文件响应:内容用 Base64,size_bytes 是解码后的字节数。"""

    path: str
    content_base64: str
    size_bytes: int


SandboxFileKind = Literal["file", "dir", "symlink", "other"]


class SandboxFileEntry(BaseModel):
    """文件树里的一个条目;path 是沙箱内绝对路径(如 /workspace/src/a.ts)。"""

    path: str
    name: str
    kind: SandboxFileKind
    size_bytes: int | None = None
    modified: float | None = None


class SandboxTreeRead(BaseModel):
    """列目录响应:entries 是扁平列表(含各级嵌套项),超上限时 truncated=true。"""

    path: str
    entries: list[SandboxFileEntry]
    truncated: bool


def ensure_workspace_path(path: str) -> str:
    """校验沙箱文件路径:必须落在 /workspace 下、不得含 .. 或 NUL。

    由服务层调用(HTTP 请求先过这里再传给 provider),失败统一 400
    `sandbox.path_invalid`——路径穿越不该表现为 500。
    """
    if "\x00" in path:
        raise AppError("sandbox.path_invalid", "文件路径不能包含 NUL 字符")
    if path != WORKSPACE_ROOT and not path.startswith(f"{WORKSPACE_ROOT}/"):
        raise AppError("sandbox.path_invalid", f"文件路径必须以 {WORKSPACE_ROOT} 开头")
    if ".." in path.split("/"):
        raise AppError("sandbox.path_invalid", "文件路径不能包含 ..")
    return path
