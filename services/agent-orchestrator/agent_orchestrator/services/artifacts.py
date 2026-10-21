"""交付物读写:按配置选择存储后端,DB 只留元数据与引用(§7 阶段 B)。

一个 run 可以有**多件**交付物(文本报告 + 技能/工具产出的文件):

- `database`(默认):文本存 `run_artifacts.content`,二进制存 `content_bytes`;
- `filesystem`:内容落本地目录,行里只留 `object_key`/`size_bytes`;
- 读端统一返回 `ArtifactView`(文本内容已装载,二进制 content 为空串),
  API 契约不随存储后端变化;下载走 `read_artifact_bytes`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from uuid import uuid4

from common.artifacts import ArtifactStore, FilesystemArtifactStore, build_artifact_key
from common.errors import NotFoundError, StorageError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run, RunArtifact
from agent_orchestrator.repositories import ArtifactRepository

DATABASE_STORE = "database"
FILESYSTEM_STORE = "filesystem"
CONTENT_TYPE = "text/markdown"
SUFFIX = ".md"
DEFAULT_CONTENT_TYPE = "application/octet-stream"

# 交付物来源:报告由交付阶段合成,skill/mcp 是技能或工具落下的真实文件
ARTIFACT_REPORT_SOURCE = "report"
ARTIFACT_SKILL_SOURCE = "skill"
ARTIFACT_MCP_SOURCE = "mcp"

_MAX_FILENAME = 200
_MAX_TITLE = 200
_MAX_SOURCE = 32
_MAX_SOURCE_URL = 1024

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,15}$")

_EXTENSION_BY_CONTENT_TYPE = {
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}
_EXTENSION_BY_KIND = {"markdown": ".md", "text": ".txt"}


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
    """交付物读模型:文本内容已从实际存储装载(二进制件 content 为空串)。"""

    id: str
    run_id: str
    kind: str
    title: str
    content: str
    storage: str
    size_bytes: int
    created_at: datetime
    filename: str | None = None
    content_type: str | None = None
    source: str = ARTIFACT_REPORT_SOURCE


def _base_content_type(content_type: str | None) -> str:
    """去掉 charset 等参数后的基础 MIME 类型(小写)。"""
    return str(content_type or "").split(";", 1)[0].strip().lower()


def is_text_content_type(content_type: str | None) -> bool:
    """是否是可直接当文本展示/入库的内容类型。

    旧行没有 content_type(阶段 A 只有 Markdown 报告)按文本兼容处理。
    """
    base = _base_content_type(content_type)
    if not base:
        return True
    if base.startswith("text/"):
        return True
    return base in {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
        "application/x-ndjson",
    }


def is_text_artifact(row: RunArtifact) -> bool:
    """该行是否是文本交付物(二进制件不进知识库、不进主文本取件)。"""
    if row.content_bytes is not None:
        return False
    return is_text_content_type(row.content_type)


def infer_kind(content_type: str | None) -> str:
    """按 content_type 推导交付物种类(image/markdown/text/file)。"""
    base = _base_content_type(content_type)
    if base.startswith("image/"):
        return "image"
    if base in {"text/markdown", "text/x-markdown"}:
        return "markdown"
    if base.startswith("text/") or base == "application/json":
        return "text"
    return "file"


def _sanitize_filename(filename: str | None) -> str:
    """净化文件名:只留 basename(去路径分隔符/控制字符),限长 200。"""
    cleaned = _CONTROL_CHARS.sub("", str(filename or ""))
    cleaned = cleaned.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "artifact"
    return cleaned[:_MAX_FILENAME]


def _safe_extension(filename: str, content_type: str | None) -> str:
    """对象键后缀:优先文件名里的安全扩展名,否则按 content_type 推导。"""
    suffix = PurePosixPath(filename).suffix.lower()
    if _SAFE_SUFFIX.fullmatch(suffix):
        return suffix
    return _EXTENSION_BY_CONTENT_TYPE.get(_base_content_type(content_type), ".bin")


def _extension_for(content_type: str | None, kind: str | None) -> str:
    base = _base_content_type(content_type)
    if base:
        return _EXTENSION_BY_CONTENT_TYPE.get(base, ".bin")
    return _EXTENSION_BY_KIND.get(str(kind or ""), ".bin")


def artifact_download_name(row: RunArtifact) -> str:
    """下载文件名:filename 优先,否则 title + 按 content_type/kind 推扩展名。"""
    if row.filename:
        return _sanitize_filename(row.filename)
    base = _sanitize_filename(row.title or "artifact")
    suffix = _extension_for(row.content_type, row.kind)
    if not suffix:
        return base
    if base.lower().endswith(suffix):
        return base
    return f"{base[: _MAX_FILENAME - len(suffix)]}{suffix}"


async def persist_artifact(
    session: AsyncSession,
    run: Run,
    artifact: dict,
    store: ArtifactStore | None,
    *,
    existing: RunArtifact | None = None,
) -> RunArtifact:
    """写文本交付物(报告):store 为 None 写 DB 列,否则写对象存储并留引用。

    existing 非空时**原地覆盖**:P3-C 的 skill_run 在 execute 阶段先登记技能输出,
    交付阶段再覆盖为最终成品——两条写入路径都走这里,不会撞唯一约束,
    也不会因已有行把 run 卡成 failed。
    """
    text = str(artifact.get("content") or "")
    data = text.encode("utf-8")
    if existing is not None:
        existing.kind = str(artifact.get("kind") or "markdown")
        existing.title = str(artifact.get("title") or run.title)
        existing.size_bytes = len(data)
        existing.filename = None
        existing.content_type = CONTENT_TYPE
        existing.source = ARTIFACT_REPORT_SOURCE
        existing.source_url = None
        existing.content_bytes = None
        if store is not None:
            key = existing.object_key or build_artifact_key(
                run.tenant_id, run.id, existing.id, suffix=SUFFIX
            )
            await store.put(key, data, content_type=CONTENT_TYPE)
            existing.storage = FILESYSTEM_STORE
            existing.object_key = key
            existing.content = None
        else:
            existing.storage = DATABASE_STORE
            existing.object_key = None
            existing.content = text
        await session.flush()
        return existing
    object_id = uuid4().hex
    row = RunArtifact(
        id=object_id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        kind=str(artifact.get("kind") or "markdown"),
        title=str(artifact.get("title") or run.title),
        filename=None,
        content_type=CONTENT_TYPE,
        source=ARTIFACT_REPORT_SOURCE,
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


async def persist_file_artifact(
    session: AsyncSession,
    run: Run,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    store: ArtifactStore | None,
    title: str | None = None,
    kind: str | None = None,
    source: str = ARTIFACT_MCP_SOURCE,
    source_url: str | None = None,
) -> RunArtifact:
    """登记一件**文件类**交付物(技能输出、外链抓取、截图等)。

    - kind 不传时按 content_type 推导(image/markdown/text/file);
    - database 模式:文本类写 `content`,二进制写 `content_bytes`;
    - filesystem 模式:对象键按 `<tenant>/<run>/<artifact><安全扩展名>` 落盘;
    - filename 会被净化(只留 basename),source 标记产出来源(默认 mcp)。
    """
    safe_name = _sanitize_filename(filename)
    resolved_content_type = str(content_type or "").strip() or DEFAULT_CONTENT_TYPE
    resolved_kind = str(kind or infer_kind(resolved_content_type))[:_MAX_SOURCE]
    resolved_title = str(title or safe_name)[:_MAX_TITLE]
    resolved_source = (
        str(source or ARTIFACT_REPORT_SOURCE).strip() or ARTIFACT_REPORT_SOURCE
    )[:_MAX_SOURCE]
    resolved_url = str(source_url)[:_MAX_SOURCE_URL] if source_url else None
    object_id = uuid4().hex
    row = RunArtifact(
        id=object_id,
        tenant_id=run.tenant_id,
        run_id=run.id,
        kind=resolved_kind,
        title=resolved_title,
        filename=safe_name,
        content_type=resolved_content_type,
        source=resolved_source,
        source_url=resolved_url,
        storage=FILESYSTEM_STORE if store is not None else DATABASE_STORE,
        size_bytes=len(data),
    )
    if store is not None:
        suffix = _safe_extension(safe_name, resolved_content_type)
        key = build_artifact_key(run.tenant_id, run.id, object_id, suffix=suffix)
        await store.put(key, data, content_type=resolved_content_type)
        row.object_key = key
    elif is_text_content_type(resolved_content_type):
        row.content = data.decode("utf-8", "replace")
    else:
        row.content_bytes = data
    session.add(row)
    await session.flush()
    return row


async def read_artifact_bytes(row: RunArtifact, store: ArtifactStore | None) -> bytes:
    """取交付物原始字节:对象存储 → content_bytes → content。"""
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
        return data
    if row.content_bytes is not None:
        return bytes(row.content_bytes)
    if row.content is not None:
        return row.content.encode("utf-8")
    return b""


async def read_artifact_text(
    row: RunArtifact, store: ArtifactStore | None
) -> str | None:
    """取文本正文;二进制件返回 None(不抛错,供入库/展示侧跳过)。"""
    if not is_text_artifact(row):
        return None
    data = await read_artifact_bytes(row, store)
    return data.decode("utf-8", "replace")


async def read_artifact(row: RunArtifact, store: ArtifactStore | None) -> ArtifactView:
    """装载交付物(文本展示用):二进制件 content 为空串,元数据照常返回。"""
    content = ""
    size = int(row.size_bytes or 0)
    if is_text_artifact(row):
        data = await read_artifact_bytes(row, store)
        content = data.decode("utf-8", "replace")
        size = size or len(data)
    return ArtifactView(
        id=row.id,
        run_id=row.run_id,
        kind=row.kind,
        title=row.title,
        content=content,
        storage=row.storage,
        size_bytes=size,
        created_at=row.created_at,
        filename=row.filename,
        content_type=row.content_type,
        source=row.source or ARTIFACT_REPORT_SOURCE,
    )


async def list_artifacts(
    session: AsyncSession, tenant_id: str, run_id: str
) -> list[RunArtifact]:
    """该 run 的全部交付物(created_at, id 升序;未交付返回空列表)。"""
    return await ArtifactRepository(session).list_for_run(tenant_id, run_id)


async def require_artifact(
    session: AsyncSession, tenant_id: str, run_id: str, artifact_id: str
) -> RunArtifact:
    """取指定交付物;不属于该 run/租户一律 404(不泄漏存在性)。"""
    row = await ArtifactRepository(session).find_artifact(
        tenant_id, run_id, artifact_id
    )
    if row is None:
        raise NotFoundError(
            "artifact.not_found",
            "交付物不存在",
            details={"run_id": run_id, "artifact_id": artifact_id},
        )
    return row
