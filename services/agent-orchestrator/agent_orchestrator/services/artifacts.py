"""交付物读写:按配置选择存储后端,DB 只留元数据与引用(§7 阶段 B)。

- `database`(默认):内容存 `run_artifacts.content` 列,零配置;
- `filesystem`:内容落本地目录,行里只留 `object_key`/`size_bytes`;
- 读端统一返回 `ArtifactView`(内容已装载),API 契约不随存储后端变化。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from common.artifacts import ArtifactStore, FilesystemArtifactStore, build_artifact_key
from common.errors import StorageError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run, RunArtifact

DATABASE_STORE = "database"
FILESYSTEM_STORE = "filesystem"
CONTENT_TYPE = "text/markdown"
SUFFIX = ".md"


def build_store(settings: Settings) -> ArtifactStore | None:
    """database 模式返回 None(内容在行里),filesystem 模式返回目录实现。"""
    if settings.artifact_store == DATABASE_STORE:
        return None
    if settings.artifact_store == FILESYSTEM_STORE:
        return FilesystemArtifactStore(settings.artifact_root)
    raise StorageError(
        "artifact.unknown_store",
        f"未知的交付物存储后端:{settings.artifact_store}",
        details={"supported": [DATABASE_STORE, FILESYSTEM_STORE]},
    )


@dataclass(frozen=True)
class ArtifactView:
    """交付物读模型:内容已从实际存储装载。"""

    id: str
    run_id: str
    kind: str
    title: str
    content: str
    storage: str
    size_bytes: int
    created_at: datetime


async def persist_artifact(
    session: AsyncSession,
    run: Run,
    artifact: dict,
    store: ArtifactStore | None,
) -> RunArtifact:
    """写交付物:store 为 None 写 DB 列,否则写对象存储并留引用。"""
    text = str(artifact.get("content") or "")
    data = text.encode("utf-8")
    object_id = uuid4().hex
    row = RunArtifact(
        id=object_id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        kind=str(artifact.get("kind") or "markdown"),
        title=str(artifact.get("title") or run.title),
        storage=FILESYSTEM_STORE if store is not None else DATABASE_STORE,
        size_bytes=len(data),
        content=None if store is not None else text,
    )
    if store is not None:
        key = build_artifact_key(run.tenant_id, run.id, object_id, suffix=SUFFIX)
        await store.put(key, data, content_type=CONTENT_TYPE)
        row.object_key = key
    session.add(row)
    await session.flush()
    return row


async def read_artifact(row: RunArtifact, store: ArtifactStore | None) -> ArtifactView:
    """装载交付物内容:有 object_key 走存储,否则读 DB 列。"""
    if row.object_key:
        if store is None:
            raise StorageError(
                "artifact.store_missing",
                "交付物在对象存储中,但当前未配置存储后端",
                details={"object_key": row.object_key},
            )
        data = await store.get(row.object_key)
        if data is None:
            raise StorageError(
                "artifact.object_missing",
                f"交付物对象不存在:{row.object_key}",
                details={"object_key": row.object_key},
            )
        content = data.decode("utf-8", "replace")
    else:
        content = row.content or ""
    return ArtifactView(
        id=row.id,
        run_id=row.run_id,
        kind=row.kind,
        title=row.title,
        content=content,
        storage=row.storage,
        size_bytes=row.size_bytes or len(content.encode("utf-8")),
        created_at=row.created_at,
    )
