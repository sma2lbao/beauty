"""LocalSandboxProvider:build 装配 + create + exec(宿主本地 provider,零网络)。

原 `tests/test_sandbox_provider_local.py` 按主题拆分:文件读写 / 目录列举 /
截断 / 销毁在 `test_sandbox_provider_local_files.py`,HTTP 生命周期端到端在
`test_sandbox_provider_local_http.py`;共享夹具在 `tests/support/sandbox_local.py`。

依赖 POSIX shell / 权限位 / 符号链接语义的用例按 sys.platform 跳过
(Windows 上 local provider 仍可用,但语义不同)。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from common.errors import UpstreamError

from tests.support.sandbox_local import (
    MAX_OUTPUT_BYTES,
    NAME,
    _dir_of,
    _spec,
    _workspace_of,
    local_root,
    posix_only,
    provider,
    sandbox,
)
from workspace.core.config import Settings
from workspace.services.sandbox_provider import (
    LOCAL_HOME_SUBDIR,
    LOCAL_TMP_SUBDIR,
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
    build_sandbox_provider,
)

# 夹具经 import 引入,由 pytest 在收集用例时解析;列入 `__all__` 以免 ruff
# 把"只在签名里用到"的夹具导入判成未使用(它们不能删)。
__all__ = ["local_root", "provider", "sandbox"]


# --- build_sandbox_provider -------------------------------------------------


def test_build_sandbox_provider_local(tmp_path: Path) -> None:
    root = tmp_path / "sb"
    settings = Settings(
        _env_file=None,
        env="dev",
        sandbox_provider="local",
        sandbox_local_root=str(root),
        sandbox_exec_timeout_seconds=7,
        sandbox_max_output_bytes=MAX_OUTPUT_BYTES,
    )

    built = build_sandbox_provider(settings)

    assert isinstance(built, LocalSandboxProvider)
    assert built.provider == "local"
    # root 在装配时解析成绝对路径:相对配置不能跟着进程 cwd 漂移
    assert built._root == root.resolve()
    assert built._max_output_bytes == MAX_OUTPUT_BYTES
    assert built._default_exec_timeout_seconds == 7


def test_build_sandbox_provider_rejects_unknown() -> None:
    settings = Settings(_env_file=None, env="dev", sandbox_provider="nope")

    with pytest.raises(UpstreamError) as excinfo:
        build_sandbox_provider(settings)

    assert excinfo.value.code == "sandbox.provider_unknown"
    assert excinfo.value.details == {"provider": "nope"}


# --- create -----------------------------------------------------------------


async def test_create_builds_workspace_home_tmp(
    provider: LocalSandboxProvider, local_root: Path
) -> None:
    await provider.create(_spec())

    base = _dir_of(local_root)
    assert base.parent == local_root.resolve()
    for subdir in (LOCAL_WORKSPACE_SUBDIR, LOCAL_HOME_SUBDIR, LOCAL_TMP_SUBDIR):
        assert (base / subdir).is_dir()


async def test_create_twice_is_rejected(
    provider: LocalSandboxProvider, local_root: Path
) -> None:
    await provider.create(_spec())

    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec())

    assert excinfo.value.code == "sandbox.provider_error"
    assert excinfo.value.details["sandbox_name"] == NAME
    # 失败不能删掉已有沙箱目录
    assert _workspace_of(local_root).is_dir()


@pytest.mark.parametrize(
    "name",
    ["../evil", ".hidden", "a" * 65, "bad name", "bad/name", "a..b"],
)
async def test_create_rejects_unsafe_name(
    provider: LocalSandboxProvider, local_root: Path, name: str
) -> None:
    with pytest.raises(UpstreamError) as excinfo:
        await provider.create(_spec(name))

    assert excinfo.value.code == "sandbox.provider_error"
    assert "sandbox_name" in excinfo.value.details
    # 校验发生在建目录之前:根目录与逃逸目标都不该出现
    assert not local_root.exists()
    assert not (local_root.parent / "evil").exists()


# --- exec -------------------------------------------------------------------


@posix_only
async def test_exec_echo(provider: LocalSandboxProvider, sandbox: str) -> None:
    result = await provider.exec(sandbox, ["echo", "hello"])

    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.duration_ms >= 0
    assert result.truncated is False


@posix_only
async def test_exec_returns_nonzero_exit_code(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    # argv 直接交给 execve(不经 shell),表达"退出码 3"只能借 sh -c;
    # /bin/sh 是 POSIX 保证存在的,所以这条用例不依赖别的外部命令
    result = await provider.exec(sandbox, ["sh", "-c", "exit 3"])

    assert result.exit_code == 3
    assert result.stdout == ""


@posix_only
async def test_exec_cwd_defaults_to_workspace(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    result = await provider.exec(sandbox, ["sh", "-c", "pwd"])

    assert result.exit_code == 0
    assert result.stdout.strip() == str(_workspace_of(local_root))


@posix_only
async def test_exec_explicit_cwd(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    await provider.write_file(sandbox, "/workspace/sub/nested.txt", b"x")

    result = await provider.exec(sandbox, ["sh", "-c", "pwd"], cwd="/workspace/sub")

    assert result.exit_code == 0
    assert result.stdout.strip() == str(_workspace_of(local_root) / "sub")


@posix_only
async def test_exec_host_absolute_cwd_passthrough(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    # 非 /workspace 前缀的绝对路径按宿主语义透传(与 microsandbox 的 cwd 透传一致)
    result = await provider.exec(sandbox, ["pwd"], cwd="/tmp")

    assert result.exit_code == 0
    assert result.stdout.strip() == str(Path("/tmp").resolve())


async def test_exec_missing_cwd_is_provider_error(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    with pytest.raises(UpstreamError) as excinfo:
        await provider.exec(sandbox, ["pwd"], cwd="/workspace/nope")

    assert excinfo.value.code == "sandbox.provider_error"
    assert excinfo.value.details["sandbox_name"] == sandbox
    assert excinfo.value.details["cwd"].endswith("/workspace/nope")


@posix_only
async def test_exec_env_defaults_and_override(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    default = await provider.exec(sandbox, ["sh", "-c", 'printf "%s" "$HOME"'])
    assert default.stdout == str(_dir_of(local_root) / LOCAL_HOME_SUBDIR)

    overridden = await provider.exec(
        sandbox,
        ["sh", "-c", 'printf "%s:%s" "$HOME" "$EXTRA"'],
        env={"HOME": "/tmp/override-home", "EXTRA": "v1"},
    )
    assert overridden.stdout == "/tmp/override-home:v1"


@posix_only
async def test_exec_passes_stdin(provider: LocalSandboxProvider, sandbox: str) -> None:
    result = await provider.exec(sandbox, ["sh", "-c", "cat"], stdin="piped-输入")

    assert result.exit_code == 0
    assert result.stdout == "piped-输入"


@posix_only
async def test_exec_timeout_raises_and_kills(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    with pytest.raises(UpstreamError) as excinfo:
        await provider.exec(sandbox, ["sleep", "2"], timeout_seconds=0.05)

    error = excinfo.value
    assert error.code == "sandbox.exec_timeout"
    assert error.details["sandbox_name"] == sandbox
    # provider 把超时写进 details 时取 int 秒,亚秒超时被截断
    assert error.details["timeout_seconds"] == int(0.05)


async def test_exec_missing_binary_is_127(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    result = await provider.exec(sandbox, ["definitely-not-a-real-binary-bm"])

    assert result.exit_code == 127
    assert result.stdout == ""
    assert result.stderr


@posix_only
async def test_exec_non_executable_file_is_126(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    await provider.write_file(sandbox, "/workspace/run.sh", b"#!/bin/sh\n")
    script = _workspace_of(local_root) / "run.sh"
    os.chmod(script, 0o644)

    result = await provider.exec(sandbox, ["./run.sh"])

    assert result.exit_code == 126
    assert result.stderr


async def test_exec_directory_argv_is_126(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    result = await provider.exec(sandbox, [str(_workspace_of(local_root))])

    assert result.exit_code == 126


async def test_exec_empty_argv_is_caller_error(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    # 协议要求 argv[0] 是可执行文件,HTTP 层已用 min_length=1 挡住;
    # 直接传空列表属于调用方编程错误,asyncio 抛 TypeError,不该伪装成 ExecResult
    with pytest.raises(TypeError):
        await provider.exec(sandbox, [])
