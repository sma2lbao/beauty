"""交付物存储:协议一致性、对象键校验、目录穿越防护、故障映射。"""

from pathlib import Path

import pytest

from common.artifacts import (
    FilesystemArtifactStore,
    build_artifact_key,
)
from common.errors import StorageError


@pytest.fixture()
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "artifacts")


async def test_put_get_delete_roundtrip(store: FilesystemArtifactStore) -> None:
    key = build_artifact_key("t-1", "run-1", "artifact-1")
    assert key == "t-1/run-1/artifact-1.md"

    size = await store.put(key, "# 周报\n".encode(), content_type="text/markdown")
    assert size == len("# 周报\n".encode())
    assert await store.get(key) == "# 周报\n".encode()

    await store.delete(key)
    assert await store.get(key) is None
    # 删除不存在的对象是幂等的
    await store.delete(key)


async def test_missing_object_returns_none(store: FilesystemArtifactStore) -> None:
    assert await store.get("t-1/run-1/nope.md") is None


async def test_nested_directories_are_created(store: FilesystemArtifactStore) -> None:
    key = build_artifact_key("t-1", "run-1", "a1", suffix=".json")
    await store.put(key, b"{}")
    assert (store.root / key).read_bytes() == b"{}"


@pytest.mark.parametrize(
    "evil",
    [
        "../outside.md",
        "t-1/../../outside.md",
        "/etc/passwd",
        "t-1/run-1/../../../outside.md",
    ],
)
async def test_path_traversal_rejected(
    store: FilesystemArtifactStore, evil: str
) -> None:
    """对象键越出存储根一律拒绝(读/写/删都拦)。"""
    with pytest.raises(StorageError) as write_error:
        await store.put(evil, b"x")
    assert write_error.value.code == "artifact.invalid_key"

    with pytest.raises(StorageError):
        await store.get(evil)
    with pytest.raises(StorageError):
        await store.delete(evil)


async def test_storage_root_itself_rejected(store: FilesystemArtifactStore) -> None:
    with pytest.raises(StorageError):
        await store.put("", b"x")


async def test_io_failure_maps_to_500(tmp_path: Path) -> None:
    """root 指向普通文件时写入失败 → StorageError(500),不裸抛 OSError。"""
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x")
    store = FilesystemArtifactStore(not_a_dir)

    with pytest.raises(StorageError) as exc_info:
        await store.put("t-1/run-1/a.md", b"x")
    assert exc_info.value.status == 500


@pytest.mark.parametrize(
    "parts",
    [
        ("t-1", "", "a1"),
        ("t-1", "..", "a1"),
        ("t-1", "run/1", "a1"),
        ("t-1", "run 1", "a1"),
    ],
)
def test_build_artifact_key_rejects_unsafe_parts(parts: tuple[str, ...]) -> None:
    with pytest.raises(StorageError):
        build_artifact_key(*parts)


def test_build_artifact_key_rejects_unsafe_suffix() -> None:
    with pytest.raises(StorageError):
        build_artifact_key("t-1", suffix="/../x")
