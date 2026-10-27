"""MicrosandboxProvider 适配层:文件读写、销毁、运行时错误与 MSB_HOME 语义。"""

from __future__ import annotations

import os

import pytest
from common.errors import NotFoundError, UpstreamError

from tests.support.sandbox_provider import (
    FakeSdkError,
    FsEntry,
    FsEntryKind,
    PathNotFoundError,
    RuntimeIncompleteError,
    RuntimeNotInstalledError,
    SandboxNotFoundError,
    _spec,
    _use_fake_sdk,
)
from workspace.core.config import Settings
from workspace.services.sandbox_provider import (
    MicrosandboxProvider,
    build_sandbox_provider,
)


async def test_read_file_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.files["/workspace/a.txt"] = b"hello"

    data = await MicrosandboxProvider().read_file("bm-abc", "/workspace/a.txt")

    assert data == b"hello"


async def test_read_file_missing_maps_to_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.read_error = PathNotFoundError("missing")

    with pytest.raises(NotFoundError) as exc_info:
        await MicrosandboxProvider().read_file("bm-abc", "/workspace/a.txt")

    assert exc_info.value.code == "sandbox.file_not_found"
    assert exc_info.value.status == 404


async def test_write_file_retries_after_mkdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.write_errors = [OSError("no such directory")]

    await MicrosandboxProvider().write_file("bm-abc", "/workspace/sub/a.txt", b"data")

    assert sdk.mkdir_calls == ["/workspace/sub"]
    assert [path for path, _ in sdk.write_calls] == ["/workspace/sub/a.txt"] * 2
    assert sdk.files["/workspace/sub/a.txt"] == b"data"


async def test_write_file_second_failure_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.write_errors = [OSError("first"), OSError("second")]

    with pytest.raises(UpstreamError) as exc_info:
        await MicrosandboxProvider().write_file(
            "bm-abc", "/workspace/sub/a.txt", b"data"
        )

    assert exc_info.value.code == "sandbox.provider_error"
    assert "OSError" in exc_info.value.details["error"]


async def test_destroy_calls_destroy_and_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)

    await MicrosandboxProvider().destroy("bm-abc")

    assert sdk.destroy_calls == [("bm-abc", True)]
    assert sdk.removed == ["bm-abc"]


async def test_destroy_is_idempotent_when_sandbox_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.get_error = SandboxNotFoundError("gone")

    await MicrosandboxProvider().destroy("bm-abc")

    assert sdk.destroy_calls == []
    assert sdk.removed == []


async def test_destroy_suppresses_remove_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.remove_error = SandboxNotFoundError("already gone")

    await MicrosandboxProvider().destroy("bm-abc")

    assert sdk.destroy_calls == [("bm-abc", True)]
    assert sdk.removed == ["bm-abc"]


@pytest.mark.parametrize(
    "error_class", [RuntimeNotInstalledError, RuntimeIncompleteError]
)
async def test_create_runtime_errors_map_to_unavailable(
    monkeypatch: pytest.MonkeyPatch, error_class: type[FakeSdkError]
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.create_error = error_class("runtime missing")

    with pytest.raises(UpstreamError) as exc_info:
        await MicrosandboxProvider().create(_spec())

    assert exc_info.value.code == "sandbox.provider_unavailable"


# --- 目录列举 ---------------------------------------------------------------


async def test_list_dir_maps_kinds_and_sorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.add_entries(
        "/workspace",
        FsEntry("/workspace/sub", FsEntryKind.DIRECTORY, size=4096, modified=10.0),
        FsEntry("/workspace/b.txt", FsEntryKind.FILE, size=5, modified=1.5),
        FsEntry("/workspace/a.txt", FsEntryKind.FILE, size=3),
        FsEntry("/workspace/link", FsEntryKind.SYMLINK, size=2),
        FsEntry("/workspace/sock", FsEntryKind.OTHER, size=0),
    )

    entries = await MicrosandboxProvider().list_dir("bm-abc", "/workspace")

    assert [entry.path for entry in entries] == [
        "/workspace/a.txt",
        "/workspace/b.txt",
        "/workspace/link",
        "/workspace/sock",
        "/workspace/sub",
    ]
    by_path = {entry.path: entry for entry in entries}
    assert [by_path[f"/workspace/{name}"].kind for name in ("a.txt", "b.txt")] == [
        "file",
        "file",
    ]
    assert by_path["/workspace/link"].kind == "symlink"
    assert by_path["/workspace/sock"].kind == "other"
    # 目录 size 归一成 0;文件保留字节数;modified 透传
    assert by_path["/workspace/sub"].size == 0
    assert by_path["/workspace/b.txt"].size == 5
    assert by_path["/workspace/b.txt"].modified == 1.5
    assert by_path["/workspace/sub"].modified == 10.0
    assert by_path["/workspace/a.txt"].modified is None
    assert sdk.list_calls == ["/workspace"]


async def test_list_dir_recurses_only_dirs_until_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.add_entries(
        "/workspace",
        FsEntry("/workspace/root.txt", FsEntryKind.FILE),
        FsEntry("/workspace/sub", FsEntryKind.DIRECTORY),
    )
    sdk.add_entries(
        "/workspace/sub",
        FsEntry("/workspace/sub/inner.txt", FsEntryKind.FILE),
        FsEntry("/workspace/sub/deep", FsEntryKind.DIRECTORY),
    )
    sdk.add_entries(
        "/workspace/sub/deep", FsEntry("/workspace/sub/deep/leaf.txt", FsEntryKind.FILE)
    )

    one = await MicrosandboxProvider().list_dir("bm-abc", "/workspace", depth=1)
    assert [entry.path for entry in one] == [
        "/workspace/root.txt",
        "/workspace/sub",
    ]

    two = await MicrosandboxProvider().list_dir("bm-abc", "/workspace", depth=2)
    assert [entry.path for entry in two] == [
        "/workspace/root.txt",
        "/workspace/sub",
        "/workspace/sub/deep",
        "/workspace/sub/inner.txt",
    ]

    three = await MicrosandboxProvider().list_dir("bm-abc", "/workspace", depth=3)
    assert [entry.path for entry in three] == [
        "/workspace/root.txt",
        "/workspace/sub",
        "/workspace/sub/deep",
        "/workspace/sub/deep/leaf.txt",
        "/workspace/sub/inner.txt",
    ]
    assert sdk.list_calls == [
        "/workspace",
        "/workspace",
        "/workspace/sub",
        "/workspace",
        "/workspace/sub",
        "/workspace/sub/deep",
    ]


async def test_list_dir_does_not_follow_symlink_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.add_entries("/workspace", FsEntry("/workspace/link", FsEntryKind.SYMLINK))
    sdk.add_entries("/workspace/link", FsEntry("/workspace/link/x.txt"))

    entries = await MicrosandboxProvider().list_dir("bm-abc", "/workspace", depth=3)

    assert [(entry.path, entry.kind) for entry in entries] == [
        ("/workspace/link", "symlink")
    ]
    assert sdk.list_calls == ["/workspace"]


async def test_list_dir_derives_name_from_path_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 真 SDK 的条目一般带 name;只给 path 时从尾段推名,完全无名的条目跳过
    sdk = _use_fake_sdk(monkeypatch)
    sdk.add_entries(
        "/workspace",
        FsEntry("/workspace/b.txt", FsEntryKind.FILE, size=1),
        FsEntry("", FsEntryKind.FILE, size=9),
    )

    entries = await MicrosandboxProvider().list_dir("bm-abc", "/workspace")

    assert [entry.path for entry in entries] == ["/workspace/b.txt"]
    assert entries[0].size == 1


async def test_list_dir_keeps_absolute_entry_paths_nested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 真 SDK 的 FsEntry 只有 path,且实现可能返回绝对路径:不能退化成按 basename
    # 拼父目录,否则 /workspace/src/a.ts 会被摊平成 /workspace/a.ts。
    sdk = _use_fake_sdk(monkeypatch)
    sdk.add_entries(
        "/workspace",
        FsEntry("/workspace/src/a.ts", FsEntryKind.DIRECTORY, modified=3.0),
    )
    sdk.add_entries(
        "/workspace/src/a.ts",
        FsEntry("/workspace/src/a.ts/deep.txt", FsEntryKind.FILE, size=7),
    )

    entries = await MicrosandboxProvider().list_dir("bm-abc", "/workspace", depth=2)

    assert [entry.path for entry in entries] == [
        "/workspace/src/a.ts",
        "/workspace/src/a.ts/deep.txt",
    ]
    assert entries[0].kind == "dir"
    assert entries[0].size == 0
    assert entries[1].size == 7
    assert sdk.list_calls == ["/workspace", "/workspace/src/a.ts"]


async def test_list_dir_resolves_relative_entry_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 也可能返回相对列举目录的名字或子路径:两种都要拼回 /workspace 下。
    sdk = _use_fake_sdk(monkeypatch)
    sdk.add_entries(
        "/workspace",
        FsEntry("a.ts", FsEntryKind.FILE, size=2),
        FsEntry("src/b.ts", FsEntryKind.FILE, size=4),
        FsEntry("src/", FsEntryKind.DIRECTORY),
    )

    entries = await MicrosandboxProvider().list_dir("bm-abc", "/workspace")

    assert [entry.path for entry in entries] == [
        "/workspace/a.ts",
        "/workspace/src",
        "/workspace/src/b.ts",
    ]


async def test_list_dir_missing_path_maps_to_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.list_error = PathNotFoundError("missing")

    with pytest.raises(NotFoundError) as exc_info:
        await MicrosandboxProvider().list_dir("bm-abc", "/workspace/nope")

    assert exc_info.value.code == "sandbox.file_not_found"
    assert exc_info.value.status == 404


async def test_list_dir_unknown_error_maps_to_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _use_fake_sdk(monkeypatch)
    sdk.list_error = OSError("boom")

    with pytest.raises(UpstreamError) as exc_info:
        await MicrosandboxProvider().list_dir("bm-abc", "/workspace")

    assert exc_info.value.code == "sandbox.provider_error"
    assert "OSError" in exc_info.value.details["error"]


def test_build_unknown_provider_raises() -> None:
    settings = Settings(env="dev", sandbox_provider="bogus")

    with pytest.raises(UpstreamError) as exc_info:
        build_sandbox_provider(settings)

    assert exc_info.value.code == "sandbox.provider_unknown"


def test_msb_home_set_before_sdk_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_HOME", raising=False)

    MicrosandboxProvider(msb_home="/tmp/ws-msb-home")

    assert os.environ["MSB_HOME"] == "/tmp/ws-msb-home"


def test_msb_home_keeps_existing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSB_HOME", "/existing")

    MicrosandboxProvider(msb_home="/other")

    assert os.environ["MSB_HOME"] == "/existing"


def test_empty_msb_home_leaves_env_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MSB_HOME", raising=False)

    MicrosandboxProvider()
