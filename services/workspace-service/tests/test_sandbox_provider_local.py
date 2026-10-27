"""LocalSandboxProvider:宿主本地 provider 的单元 + 经 HTTP 生命周期端到端。

只覆盖 local 分支(build 装配 + create/exec/文件/destroy);沙箱根一律落在
tmp_path 下,零网络。依赖 POSIX shell / 权限位 / 符号链接语义的用例按
sys.platform 跳过(Windows 上 local provider 仍可用,但语义不同)。
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest
from common.errors import AppError, NotFoundError, UpstreamError
from fastapi.testclient import TestClient

from tests.conftest import TENANT, _settings, verifier
from workspace.core.config import Settings
from workspace.main import create_app
from workspace.services.sandbox_provider import (
    LOCAL_HOME_SUBDIR,
    LOCAL_TMP_SUBDIR,
    LOCAL_WORKSPACE_SUBDIR,
    LocalSandboxProvider,
    SandboxSpec,
    build_sandbox_provider,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32",
    reason="用例依赖 POSIX shell / 权限位 / 符号链接语义",
)

NAME = "bm-local"
MAX_OUTPUT_BYTES = 1024


def _spec(name: str = NAME) -> SandboxSpec:
    return SandboxSpec(
        tenant_id="t-1",
        name=name,
        kind="generic",
        image="python",
        cpus=1.0,
        memory_mb=512,
        network="deny",
        ttl_seconds=600,
    )


def _dir_of(root: Path, name: str = NAME) -> Path:
    """沙箱宿主目录;按 provider 的 resolve 语义对齐(macOS /var→/private/var)。"""
    return (root / name).resolve()


def _workspace_of(root: Path, name: str = NAME) -> Path:
    return _dir_of(root, name) / LOCAL_WORKSPACE_SUBDIR


@pytest.fixture()
def local_root(tmp_path: Path) -> Path:
    return tmp_path / "sb"


@pytest.fixture()
def provider(local_root: Path) -> LocalSandboxProvider:
    return LocalSandboxProvider(
        root=str(local_root),
        max_output_bytes=MAX_OUTPUT_BYTES,
        default_exec_timeout_seconds=7,
    )


@pytest.fixture()
async def sandbox(provider: LocalSandboxProvider) -> str:
    await provider.create(_spec())
    return NAME


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


# --- 文件读写 ---------------------------------------------------------------


async def test_write_read_roundtrip_nested_binary(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    payload = b"\x00\x01\x02binary\n"

    await provider.write_file(sandbox, "/workspace/a/b.txt", payload)

    assert await provider.read_file(sandbox, "/workspace/a/b.txt") == payload
    assert (_workspace_of(local_root) / "a" / "b.txt").read_bytes() == payload


async def test_read_missing_file_is_not_found(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        await provider.read_file(sandbox, "/workspace/nope.txt")

    assert excinfo.value.code == "sandbox.file_not_found"
    assert excinfo.value.details == {
        "sandbox_name": sandbox,
        "path": "/workspace/nope.txt",
    }


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/workspace/../etc/passwd", "/workspace/a\x00b", "workspace/a"],
)
async def test_invalid_paths_are_rejected(
    provider: LocalSandboxProvider, sandbox: str, path: str
) -> None:
    with pytest.raises(AppError) as write_exc:
        await provider.write_file(sandbox, path, b"x")
    assert write_exc.value.code == "sandbox.path_invalid"

    with pytest.raises(AppError) as read_exc:
        await provider.read_file(sandbox, path)
    assert read_exc.value.code == "sandbox.path_invalid"


@posix_only
async def test_symlink_escape_is_rejected(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    workspace = _workspace_of(local_root)
    (workspace / "evil").symlink_to("/etc", target_is_directory=True)

    with pytest.raises(AppError) as excinfo:
        await provider.read_file(sandbox, "/workspace/evil/passwd")

    assert excinfo.value.code == "sandbox.path_invalid"


# --- 目录列举 ---------------------------------------------------------------


async def test_list_dir_marks_kinds_and_sorts(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    workspace = _workspace_of(local_root)
    (workspace / "a.txt").write_bytes(b"123")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "b.txt").write_bytes(b"x")

    entries = await provider.list_dir(sandbox, "/workspace")

    assert [entry.path for entry in entries] == [
        "/workspace/a.txt",
        "/workspace/sub",
    ]
    by_path = {entry.path: entry for entry in entries}
    assert by_path["/workspace/a.txt"].kind == "file"
    assert by_path["/workspace/a.txt"].size == 3
    assert isinstance(by_path["/workspace/a.txt"].modified, float)
    assert by_path["/workspace/sub"].kind == "dir"
    assert by_path["/workspace/sub"].size == 0


@posix_only
async def test_list_dir_marks_symlink_without_following(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    workspace = _workspace_of(local_root)
    (workspace / "sub").mkdir()
    (workspace / "sub" / "inner.txt").write_bytes(b"x")
    (workspace / "linkdir").symlink_to(workspace / "sub", target_is_directory=True)
    (workspace / "link.txt").symlink_to(workspace / "sub" / "inner.txt")

    entries = await provider.list_dir(sandbox, "/workspace", depth=3)

    by_path = {entry.path: entry for entry in entries}
    assert by_path["/workspace/linkdir"].kind == "symlink"
    assert by_path["/workspace/link.txt"].kind == "symlink"
    # 符号链接只标记、不进入:不会有 /workspace/linkdir/... 的条目
    assert not [path for path in by_path if path.startswith("/workspace/linkdir/")]
    assert by_path["/workspace/sub/inner.txt"].kind == "file"


async def test_list_dir_respects_depth(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    workspace = _workspace_of(local_root)
    (workspace / "sub").mkdir()
    (workspace / "sub" / "c.txt").write_bytes(b"c")
    (workspace / "sub" / "deep").mkdir()
    (workspace / "sub" / "deep" / "d.txt").write_bytes(b"d")

    one = await provider.list_dir(sandbox, "/workspace", depth=1)
    assert [entry.path for entry in one] == ["/workspace/sub"]

    two = await provider.list_dir(sandbox, "/workspace", depth=2)
    assert [entry.path for entry in two] == [
        "/workspace/sub",
        "/workspace/sub/c.txt",
        "/workspace/sub/deep",
    ]

    three = await provider.list_dir(sandbox, "/workspace", depth=3)
    assert three[-1].path == "/workspace/sub/deep/d.txt"


async def test_list_dir_missing_dir_is_not_found(
    provider: LocalSandboxProvider, sandbox: str
) -> None:
    with pytest.raises(NotFoundError) as excinfo:
        await provider.list_dir(sandbox, "/workspace/nope")

    assert excinfo.value.code == "sandbox.file_not_found"
    assert excinfo.value.details == {
        "sandbox_name": sandbox,
        "path": "/workspace/nope",
    }


async def test_list_dir_on_missing_sandbox_is_provider_error(
    provider: LocalSandboxProvider,
) -> None:
    with pytest.raises(UpstreamError) as excinfo:
        await provider.list_dir(NAME, "/workspace")

    assert excinfo.value.code == "sandbox.provider_error"
    assert excinfo.value.details["sandbox_name"] == NAME


@pytest.mark.parametrize("path", ["/etc", "/workspace/../etc", "workspace/a"])
async def test_list_dir_rejects_invalid_path(
    provider: LocalSandboxProvider, sandbox: str, path: str
) -> None:
    with pytest.raises(AppError) as excinfo:
        await provider.list_dir(sandbox, path)

    assert excinfo.value.code == "sandbox.path_invalid"


# --- 沙箱不存在 / 截断 / 销毁 ----------------------------------------------


async def test_operations_on_missing_sandbox_are_provider_errors(
    provider: LocalSandboxProvider,
) -> None:
    with pytest.raises(UpstreamError) as exec_exc:
        await provider.exec(NAME, ["echo"])
    assert exec_exc.value.code == "sandbox.provider_error"
    assert exec_exc.value.details["sandbox_name"] == NAME

    with pytest.raises(UpstreamError) as write_exc:
        await provider.write_file(NAME, "/workspace/a.txt", b"x")
    assert write_exc.value.code == "sandbox.provider_error"
    assert write_exc.value.details["sandbox_name"] == NAME

    with pytest.raises(UpstreamError) as read_exc:
        await provider.read_file(NAME, "/workspace/a.txt")
    assert read_exc.value.code == "sandbox.provider_error"
    assert read_exc.value.details["sandbox_name"] == NAME


@posix_only
async def test_output_is_truncated(local_root: Path) -> None:
    provider = LocalSandboxProvider(root=str(local_root), max_output_bytes=16)
    await provider.create(_spec())

    result = await provider.exec(NAME, ["sh", "-c", "printf '0123456789abcdefghij'"])

    assert result.truncated is True
    assert result.stdout == "0123456789abcdef"
    assert len(result.stdout.encode("utf-8")) == 16


async def test_destroy_removes_dir_and_is_idempotent(
    provider: LocalSandboxProvider, sandbox: str, local_root: Path
) -> None:
    base = _dir_of(local_root)
    assert base.is_dir()

    await provider.destroy(sandbox)
    assert not base.exists()

    # 运行时已不存在也静默成功(业务层回收/重启后重试都会走到这里)
    await provider.destroy(sandbox)
    await provider.destroy("bm-never-created")


# --- HTTP 端到端(不注入 provider,走 lifespan 的 build 分支) ----------------


@posix_only
def test_http_local_provider_lifecycle(tmp_path: Path) -> None:
    local_root = tmp_path / "sb"
    settings = _settings(
        tmp_path,
        "local.db",
        sandbox_provider="local",
        sandbox_local_root=str(local_root),
    )
    app = create_app(settings, token_verifier=verifier())

    with TestClient(app) as client:
        # lifespan 走 build_sandbox_provider,并打 local 隔离告警
        assert isinstance(app.state.sandbox_provider, LocalSandboxProvider)
        assert app.state.sandbox_provider.provider == "local"

        created = client.post("/v1/sandboxes", json={}, headers=TENANT)
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["provider"] == "local"
        name = body["name"]
        sandbox_dir = _dir_of(local_root, name)
        assert (sandbox_dir / LOCAL_WORKSPACE_SUBDIR).is_dir()

        executed = client.post(
            f"/v1/sandboxes/{body['id']}/exec",
            json={"argv": ["sh", "-c", "printf 'from-local'"]},
            headers=TENANT,
        )
        assert executed.status_code == 200, executed.text
        assert executed.json()["exit_code"] == 0
        assert executed.json()["stdout"] == "from-local"
        assert executed.json()["truncated"] is False

        payload = b"e2e-bytes\n"
        written = client.put(
            f"/v1/sandboxes/{body['id']}/files",
            json={
                "path": "/workspace/notes/hello.txt",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            },
            headers=TENANT,
        )
        assert written.status_code == 204
        read = client.get(
            f"/v1/sandboxes/{body['id']}/files",
            params={"path": "/workspace/notes/hello.txt"},
            headers=TENANT,
        )
        assert read.status_code == 200
        assert base64.b64decode(read.json()["content_base64"]) == payload
        assert read.json()["size_bytes"] == len(payload)
        assert (sandbox_dir / "workspace/notes/hello.txt").read_bytes() == payload

        # 执行现场:根目录只列直接子项,进一层才看到文件
        root_tree = client.get(
            f"/v1/sandboxes/{body['id']}/tree", headers=TENANT
        ).json()
        assert root_tree["path"] == "/workspace"
        assert [(item["name"], item["kind"]) for item in root_tree["entries"]] == [
            ("notes", "dir")
        ]

        notes_tree = client.get(
            f"/v1/sandboxes/{body['id']}/tree",
            params={"path": "/workspace/notes", "depth": 2},
            headers=TENANT,
        )
        assert notes_tree.status_code == 200, notes_tree.text
        assert notes_tree.json()["truncated"] is False
        assert [
            (item["name"], item["kind"], item["size_bytes"])
            for item in notes_tree.json()["entries"]
        ] == [("hello.txt", "file", len(payload))]

        deleted = client.delete(f"/v1/sandboxes/{body['id']}", headers=TENANT)
        assert deleted.status_code == 204
        assert not sandbox_dir.exists()
