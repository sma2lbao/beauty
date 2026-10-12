"""交付物内容存储(架构 §7 阶段 B):数据库列之外的对象存储抽象。

约定:

- `ArtifactStore` 协议只有三个动作(put/get/delete),S3/MinIO 等实现
  只需满足同一协议,调用方零改动;
- 对象键由 `build_artifact_key` 生成(只允许安全字符),实现侧仍会
  二次校验:`.resolve()` 后必须落在存储根内——**防目录穿越**;
- 文件 IO 走 `asyncio.to_thread`,不阻塞事件循环;
- 存储故障是 500(`StorageError`),不是调用方的错。
"""

from __future__ import annotations

import re
from asyncio import to_thread
from pathlib import Path
from typing import Protocol

from common.errors import StorageError

DEFAULT_CONTENT_TYPE = "application/octet-stream"

_SAFE_PART = re.compile(r"^[A-Za-z0-9._-]+$")


def build_artifact_key(*parts: str, suffix: str = ".md") -> str:
    """拼对象键(如 ``<tenant>/<run>/<artifact>.md``)。

    各段只允许 ``[A-Za-z0-9._-]``,拒绝空段与 ``.``/``..``——
    对象键永远不直接采用外部输入原文。
    """
    if not _SAFE_PART.match(suffix) or suffix.startswith(".."):
        raise StorageError("artifact.invalid_key", f"非法后缀:{suffix!r}")
    cleaned: list[str] = []
    for part in parts:
        text = str(part).strip()
        if not text or text in {".", ".."} or not _SAFE_PART.match(text):
            raise StorageError("artifact.invalid_key", f"非法对象键片段:{part!r}")
        cleaned.append(text)
    return "/".join(cleaned) + suffix


class ArtifactStore(Protocol):
    """交付物内容存储协议。"""

    async def put(
        self, key: str, data: bytes, *, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> int:
        """写入对象,返回字节数。"""
        ...

    async def get(self, key: str) -> bytes | None:
        """读取对象;不存在返回 None。"""
        ...

    async def delete(self, key: str) -> None:
        """删除对象;不存在视为成功(幂等)。"""
        ...


DATABASE_BACKEND = "database"
FILESYSTEM_BACKEND = "filesystem"


def build_artifact_store(
    backend: str, *, root: str = "./.artifacts"
) -> ArtifactStore | None:
    """按配置选后端:``database`` → None(内容随行存 DB 列);``filesystem`` → 本地目录。

    未知后端直接报错(启动即失败,不带病上线)。
    """
    if backend == DATABASE_BACKEND:
        return None
    if backend == FILESYSTEM_BACKEND:
        return FilesystemArtifactStore(root)
    raise StorageError(
        "artifact.unknown_store",
        f"未知的交付物存储后端:{backend}",
        details={"supported": [DATABASE_BACKEND, FILESYSTEM_BACKEND]},
    )


class FilesystemArtifactStore:
    """本地目录实现:单机部署与开发用;换 S3 兼容存储时调用方不动。"""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    async def put(
        self, key: str, data: bytes, *, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> int:
        path = self._resolve(key)
        try:
            await to_thread(self._write, path, data)
        except OSError as exc:
            raise StorageError("artifact.put_failed", f"写入交付物失败:{exc}") from exc
        return len(data)

    async def get(self, key: str) -> bytes | None:
        path = self._resolve(key)
        try:
            return await to_thread(self._read, path)
        except OSError as exc:
            raise StorageError("artifact.get_failed", f"读取交付物失败:{exc}") from exc

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        try:
            await to_thread(self._unlink, path)
        except OSError as exc:
            raise StorageError(
                "artifact.delete_failed", f"删除交付物失败:{exc}"
            ) from exc

    def _resolve(self, key: str) -> Path:
        """把对象键安全地落到 root 内;越界即拒绝(防目录穿越)。"""
        candidate = (self._root / key).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise StorageError("artifact.invalid_key", f"对象键越出存储根:{key!r}")
        return candidate

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def _read(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
