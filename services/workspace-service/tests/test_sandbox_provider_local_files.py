"""LocalSandboxProvider 的文件读写 / 目录列举 / 截断 / 销毁(拆分自原文件)。"""

from __future__ import annotations

from pathlib import Path

import pytest
from common.errors import AppError, NotFoundError, UpstreamError

from tests.support.sandbox_local import (
    NAME,
    _dir_of,
    _spec,
    _workspace_of,
    local_root,
    posix_only,
    provider,
    sandbox,
)
from workspace.services.sandbox_provider import (
    LocalSandboxProvider,
)

# 夹具经 import 引入,由 pytest 在收集用例时解析;列入 `__all__` 以免 ruff
# 把"只在签名里用到"的夹具导入判成未使用(它们不能删)。
__all__ = ["local_root", "provider", "sandbox"]


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
