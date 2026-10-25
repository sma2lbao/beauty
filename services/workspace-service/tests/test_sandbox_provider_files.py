"""MicrosandboxProvider 适配层:文件读写、销毁、运行时错误与 MSB_HOME 语义。"""

from __future__ import annotations

import os

import pytest
from common.errors import NotFoundError, UpstreamError

from tests.support.sandbox_provider import (
    FakeSdkError,
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
