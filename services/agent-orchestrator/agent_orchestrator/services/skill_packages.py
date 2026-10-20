"""技能包(P3):用户上传 SKILL.md 包的校验/存储/版本化与 run 快照。

包是 zip(单包 20MB / 500 文件 / 解压后 50MB 上限),根目录必须含 SKILL.md,
允许只有单一顶层目录(服务端归一化后判断);可选 skill.json 描述 id/version/
title/description/entrypoint/runtime/network/timeout_seconds/tools。解析只依赖
标准库(zipfile/json),zip 字节交给 workspace-service 的通用对象 API(Workspace
协议),DB 只存引用、manifest 快照与 SKILL.md 全文。

run 创建时通过 resolve_skill 拿 SkillDefinition:内置技能优先,其次租户上传包的
最新版本;执行字段(entrypoint/runtime/network/timeout)与对象 key/sha 一并冻结进
runs 行,P3-C 的 skill_run 工具据此取包执行。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from common.errors import AppError, ConflictError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.skills import SkillPackage, SkillVersion
from agent_orchestrator.repositories import (
    SkillPackageRepository,
    SkillVersionRepository,
)
from agent_orchestrator.schemas.skills import (
    SkillManifest,
    SkillPackageRead,
    SkillVersionRead,
)
from agent_orchestrator.services.skills import SkillRegistry
from agent_orchestrator.services.workspace import Workspace

logger = structlog.get_logger(__name__)

#: 包体上限(上传字节)
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
#: 文件数上限
MAX_FILES = 500
#: 解压后总字节上限(压缩炸弹防线)
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
#: SKILL.md 进快照时的截断长度(与 SkillManifest.system_prompt 上限一致)
MAX_SKILL_MD_CHARS = 20000
MAX_TITLE_CHARS = 120
MAX_DESCRIPTION_CHARS = 2000
MAX_VERSION_CHARS = 32
#: slug 列宽(skill_packages.slug=80);id pattern 允许到 81,这里按列宽收口
MAX_SLUG_CHARS = 80
DEFAULT_VERSION = "1.0.0"
DEFAULT_TIMEOUT_SECONDS = 300
MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 1800
RUNTIMES = frozenset({"python", "node"})
NETWORKS = frozenset({"deny", "public"})
SKILL_MD = "SKILL.md"
MANIFEST_JSON = "skill.json"
#: slug 契约与 SkillManifest.id 一致
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,80}$")
ACTIVE = "active"
#: 有 entrypoint 的技能自动获得这个工具(P3-C 在沙箱内执行技能包)
SKILL_RUN_TOOL = "skill_run"


class SkillStorageUnavailable(AppError):
    """对象存储未装配/不可用:明确报不可用,不静默降级。"""

    status = 503


@dataclass(frozen=True)
class ParsedPackage:
    """校验/归一化后的上传包(纯数据,不碰 DB/网络)。"""

    slug: str
    version: str
    title: str
    description: str
    entrypoint: list[str] | None
    runtime: str
    network: str
    timeout_seconds: int
    tools: list[str]
    skill_md: str
    file_count: int
    manifest: dict[str, Any]


@dataclass(frozen=True)
class SkillDefinition:
    """run 创建时冻结的技能定义(P3-C 执行 skill 包用)。"""

    manifest: SkillManifest
    package_id: str | None = None
    version_id: str | None = None
    object_key: str | None = None
    sha256: str | None = None
    entrypoint: list[str] | None = None
    runtime: str | None = None
    network: str | None = None
    timeout_seconds: int | None = None


def _too_large(message: str) -> AppError:
    return AppError("skill.package_too_large", message)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    """zip 里符号链接靠 unix 权限位标记(S_IFLNK),一律拒绝。"""
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _validate_entry(name: str) -> str:
    """归一化包内路径;拒绝绝对路径/穿越/NUL(服务端只认 zip 内的相对路径)。"""
    if not name or "\x00" in name:
        raise AppError("skill.path_invalid", "包内存在空路径或 NUL 字符")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise AppError("skill.path_invalid", f"不允许绝对路径:{name}")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise AppError("skill.path_invalid", f"不允许路径穿越:{name}")
    if not parts:
        raise AppError("skill.path_invalid", f"非法路径:{name}")
    return "/".join(parts)


def _strip_single_root(names: list[str]) -> list[str]:
    """允许 zip 只包一层顶层目录:根目录已有 SKILL.md 时不剥。"""
    if SKILL_MD in names:
        return names
    tops = {name.split("/")[0] for name in names}
    if len(tops) != 1:
        return names
    top = next(iter(tops))
    if all("/" in name for name in names):
        return [name[len(top) + 1 :] for name in names]
    return names


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, budget: int) -> bytes:
    """按块读成员并守住解压总量上限(不信任 zip 头里的 file_size)。"""
    chunks: list[bytes] = []
    total = 0
    with archive.open(info) as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > budget:
                raise _too_large(f"技能包解压后超过 {MAX_UNCOMPRESSED_BYTES} 字节")
            chunks.append(chunk)
    return b"".join(chunks)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _manifest_invalid(message: str) -> AppError:
    return AppError("skill.manifest_invalid", f"skill.json 非法:{message}")


def _slug_from_filename(filename: str | None) -> str:
    stem = Path(filename or "skill").stem
    candidate = re.sub(r"[^a-z0-9._-]+", "-", stem.lower())
    candidate = re.sub(r"^[^a-z0-9]+", "", candidate).strip("-._")
    if len(candidate) > MAX_SLUG_CHARS:
        candidate = candidate[:MAX_SLUG_CHARS].strip("-._")
    if not SLUG_PATTERN.match(candidate):
        raise AppError(
            "skill.id_invalid",
            "无法从文件名推导技能 id,请在 skill.json 中提供合法 id",
            details={"filename": filename or ""},
        )
    return candidate


def _title_from_markdown(skill_md: str) -> str:
    for line in skill_md.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if match:
            return match.group(1)[:MAX_TITLE_CHARS]
    return ""


def _validate_entrypoint(entrypoint: list[str]) -> list[str]:
    """入口命令不接受 NUL/绝对路径/穿越(执行发生在沙箱内也应守住包边界)。"""
    for arg in entrypoint:
        if "\x00" in arg:
            raise _manifest_invalid("entrypoint 含 NUL 字符")
        candidate = arg.replace("\\", "/")
        if candidate.startswith("/") or re.match(r"^[A-Za-z]:", candidate):
            raise _manifest_invalid(f"entrypoint 不允许绝对路径:{arg}")
        if ".." in candidate.split("/"):
            raise _manifest_invalid(f"entrypoint 不允许路径穿越:{arg}")
    return list(entrypoint)


def _parse_manifest(
    payload: dict[str, Any], skill_md: str, filename: str | None
) -> ParsedPackage:
    """skill.json + SKILL.md + 文件名 → 归一化元数据(缺省值见契约)。"""
    raw_id = payload.get("id")
    if raw_id is None:
        slug = _slug_from_filename(filename)
    elif not isinstance(raw_id, str) or not SLUG_PATTERN.match(raw_id.strip()):
        raise AppError("skill.id_invalid", f"技能 id 不合法:{raw_id!r}")
    else:
        slug = raw_id.strip()
    if len(slug) > MAX_SLUG_CHARS:
        raise AppError(
            "skill.id_invalid",
            f"技能 id 超过 {MAX_SLUG_CHARS} 字符",
            details={"skill_id": slug},
        )

    raw_version = payload.get("version", DEFAULT_VERSION)
    if not isinstance(raw_version, str) or not raw_version.strip():
        raise _manifest_invalid("version 必须是非空字符串")
    version = raw_version.strip()
    if len(version) > MAX_VERSION_CHARS:
        raise _manifest_invalid(f"version 超过 {MAX_VERSION_CHARS} 字符")

    raw_title = payload.get("title")
    if raw_title is not None and not isinstance(raw_title, str):
        raise _manifest_invalid("title 必须是字符串")
    title = (raw_title or "").strip()
    if not title:
        title = _title_from_markdown(skill_md) or Path(filename or slug).stem
    title = title[:MAX_TITLE_CHARS] or slug

    raw_description = payload.get("description", "")
    if not isinstance(raw_description, str):
        raise _manifest_invalid("description 必须是字符串")
    description = raw_description.strip()[:MAX_DESCRIPTION_CHARS]

    raw_entrypoint = payload.get("entrypoint")
    if raw_entrypoint is None:
        entrypoint: list[str] | None = None
    elif (
        not isinstance(raw_entrypoint, list)
        or not raw_entrypoint
        or not all(isinstance(arg, str) and arg for arg in raw_entrypoint)
    ):
        raise _manifest_invalid("entrypoint 必须是非空字符串数组")
    else:
        entrypoint = _validate_entrypoint([str(arg) for arg in raw_entrypoint])

    runtime = payload.get("runtime", "python")
    if runtime not in RUNTIMES:
        raise _manifest_invalid(f"runtime 必须是 {sorted(RUNTIMES)}")

    network = payload.get("network", "deny")
    if network not in NETWORKS:
        raise _manifest_invalid(f"network 必须是 {sorted(NETWORKS)}")

    timeout_seconds = payload.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise _manifest_invalid("timeout_seconds 必须是整数")
    if not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise _manifest_invalid(
            f"timeout_seconds 必须在 {MIN_TIMEOUT_SECONDS}..{MAX_TIMEOUT_SECONDS}"
        )

    raw_tools = payload.get("tools", [])
    if not isinstance(raw_tools, list) or not all(
        isinstance(tool, str) and tool for tool in raw_tools
    ):
        raise _manifest_invalid("tools 必须是字符串数组")
    tools = _dedupe(
        [*[str(tool) for tool in raw_tools], *([SKILL_RUN_TOOL] if entrypoint else [])]
    )

    manifest: dict[str, Any] = {
        "id": slug,
        "version": version,
        "title": title,
        "description": description,
        "entrypoint": entrypoint,
        "runtime": runtime,
        "network": network,
        "timeout_seconds": timeout_seconds,
        "tools": tools,
        "has_scripts": bool(entrypoint),
    }
    return ParsedPackage(
        slug=slug,
        version=version,
        title=title,
        description=description,
        entrypoint=entrypoint,
        runtime=runtime,
        network=network,
        timeout_seconds=timeout_seconds,
        tools=tools,
        skill_md=skill_md,
        file_count=0,
        manifest=manifest,
    )


def parse_package(data: bytes, filename: str | None = None) -> ParsedPackage:
    """校验 zip 并解析包元数据;所有不合规输入 → 4xx AppError(不落库)。"""
    if not data:
        raise AppError("skill.package_invalid", "技能包为空")
    if len(data) > MAX_PACKAGE_BYTES:
        raise _too_large(f"技能包超过 {MAX_PACKAGE_BYTES} 字节")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise AppError("skill.package_invalid", "不是合法的 zip 包") from exc
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        if not entries:
            raise AppError("skill.package_invalid", "技能包内没有文件")
        if len(entries) > MAX_FILES:
            raise _too_large(f"技能包文件数超过 {MAX_FILES}")
        declared = sum(info.file_size for info in entries)
        if declared > MAX_UNCOMPRESSED_BYTES:
            raise _too_large(f"技能包解压后超过 {MAX_UNCOMPRESSED_BYTES} 字节")

        mapping: dict[str, zipfile.ZipInfo] = {}
        for info in entries:
            if _is_symlink(info):
                raise AppError("skill.path_invalid", f"不允许符号链接:{info.filename}")
            normalized = _validate_entry(info.filename)
            if normalized in mapping:
                raise AppError("skill.path_invalid", f"重复路径:{normalized}")
            mapping[normalized] = info

        names = list(mapping)
        stripped = _strip_single_root(names)
        if stripped != names:
            mapping = {
                stripped[index]: mapping[name] for index, name in enumerate(names)
            }
            names = stripped
        if SKILL_MD not in mapping:
            raise AppError("skill.skill_md_missing", "技能包根目录缺少 SKILL.md")

        contents: dict[str, bytes] = {}
        budget = MAX_UNCOMPRESSED_BYTES
        for name in names:
            member = _read_member(archive, mapping[name], budget)
            budget -= len(member)
            contents[name] = member

    skill_md = contents[SKILL_MD].decode("utf-8", errors="replace")
    skill_md = skill_md[:MAX_SKILL_MD_CHARS]

    payload: dict[str, Any] = {}
    if MANIFEST_JSON in contents:
        try:
            payload = json.loads(contents[MANIFEST_JSON].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _manifest_invalid(f"不是合法 JSON:{exc}") from exc
        if not isinstance(payload, dict):
            raise _manifest_invalid("顶层必须是对象")

    parsed = _parse_manifest(payload, skill_md, filename)
    return replace(parsed, file_count=len(contents))


def _version_read(version: SkillVersion) -> SkillVersionRead:
    return SkillVersionRead(
        id=version.id,
        version=version.version,
        size_bytes=version.size_bytes,
        sha256=version.sha256,
        entrypoint=list(version.entrypoint) if version.entrypoint else None,
        runtime=version.runtime,
        network=version.network,
        timeout_seconds=version.timeout_seconds,
        has_scripts=bool(version.entrypoint),
        created_at=version.created_at,
    )


def _package_read(
    package: SkillPackage, versions: list[SkillVersion]
) -> SkillPackageRead:
    return SkillPackageRead(
        id=package.id,
        slug=package.slug,
        title=package.title,
        description=package.description,
        latest_version=versions[0].version if versions else None,
        created_at=package.created_at,
        updated_at=package.updated_at,
        versions=[_version_read(version) for version in versions],
    )


def _manifest_for(package: SkillPackage, version: SkillVersion) -> SkillManifest:
    snapshot = version.manifest or {}
    tools = list(snapshot.get("tools") or [])
    if version.entrypoint and SKILL_RUN_TOOL not in tools:
        tools.append(SKILL_RUN_TOOL)
    return SkillManifest(
        id=package.slug,
        version=version.version,
        title=package.title,
        description=package.description or "",
        tools=tools,
        system_prompt=version.skill_md,
        source="uploaded",
        has_scripts=bool(version.entrypoint),
        package_id=package.id,
        version_id=version.id,
    )


def _definition(package: SkillPackage, version: SkillVersion) -> SkillDefinition:
    return SkillDefinition(
        manifest=_manifest_for(package, version),
        package_id=package.id,
        version_id=version.id,
        object_key=version.object_key,
        sha256=version.sha256,
        entrypoint=list(version.entrypoint) if version.entrypoint else None,
        runtime=version.runtime,
        network=version.network,
        timeout_seconds=version.timeout_seconds,
    )


async def create_package(
    session: AsyncSession,
    tenant_id: str,
    data: bytes,
    filename: str | None,
    *,
    workspace: Workspace | None = None,
    registry: SkillRegistry | None = None,
) -> SkillPackageRead:
    """校验 → 解析 → put_object → upsert package/version,返回包详情。

    幂等口径:同包同版本重复上传 → 409 skill.version_exists;
    上传包 slug 与内置技能 id 冲突 → 409 skill.id_conflict;
    对象存储未装配 → 503 skill.storage_unavailable(不静默降级)。
    """
    parsed = parse_package(data, filename)
    if registry is not None and registry.get(parsed.slug) is not None:
        raise ConflictError(
            "skill.id_conflict",
            f"技能 id {parsed.slug} 与内置技能冲突",
            details={"skill_id": parsed.slug},
        )

    packages = SkillPackageRepository(session)
    versions = SkillVersionRepository(session)
    package = await packages.find_by_slug(tenant_id, parsed.slug)
    if package is not None and (
        await versions.find_version(tenant_id, package.id, parsed.version)
    ):
        raise ConflictError(
            "skill.version_exists",
            f"技能 {parsed.slug} 版本 {parsed.version} 已存在",
            details={"skill_id": parsed.slug, "version": parsed.version},
        )
    if workspace is None:
        raise SkillStorageUnavailable(
            "skill.storage_unavailable",
            "对象存储未配置,不能上传技能包",
        )

    stored = await workspace.put_object(
        tenant_id,
        data,
        filename=filename,
        content_type="application/zip",
    )
    if package is None:
        package = SkillPackage(
            tenant_id=tenant_id,
            slug=parsed.slug,
            title=parsed.title,
            description=parsed.description,
            status=ACTIVE,
        )
        await packages.add(package)
    else:
        package.title = parsed.title
        package.description = parsed.description
        # 新版本也算包的一次更新(列表按 updated_at 排最新在前)
        package.updated_at = datetime.now(UTC)
        await session.flush()

    version = SkillVersion(
        tenant_id=tenant_id,
        package_id=package.id,
        version=parsed.version,
        object_key=str(stored["key"]),
        sha256=str(stored["sha256"]),
        size_bytes=int(stored["size_bytes"]),
        manifest=parsed.manifest,
        skill_md=parsed.skill_md,
        entrypoint=parsed.entrypoint,
        runtime=parsed.runtime,
        network=parsed.network,
        timeout_seconds=parsed.timeout_seconds,
    )
    await versions.add(version)
    logger.info(
        "skill package uploaded",
        tenant_id=tenant_id,
        slug=parsed.slug,
        version=parsed.version,
        file_count=parsed.file_count,
        has_scripts=bool(parsed.entrypoint),
    )
    all_versions = await versions.list_for_package(tenant_id, package.id)
    return _package_read(package, all_versions)


async def list_packages(
    session: AsyncSession, tenant_id: str
) -> list[SkillPackageRead]:
    """租户内全部技能包(含各自全部版本,最新版本在 versions[0])。"""
    packages = await SkillPackageRepository(session).list_for_tenant(tenant_id)
    versions = SkillVersionRepository(session)
    result: list[SkillPackageRead] = []
    for package in packages:
        result.append(
            _package_read(
                package, await versions.list_for_package(tenant_id, package.id)
            )
        )
    return result


async def get_package(
    session: AsyncSession, tenant_id: str, package_id: str
) -> SkillPackageRead:
    """按 id 取包(跨租户 404,由仓储统一语义)。"""
    package = await SkillPackageRepository(session).require_for_tenant(
        tenant_id, package_id
    )
    versions = await SkillVersionRepository(session).list_for_package(
        tenant_id, package.id
    )
    return _package_read(package, versions)


async def delete_package(
    session: AsyncSession,
    tenant_id: str,
    package_id: str,
    *,
    workspace: Workspace | None = None,
) -> None:
    """删除 DB 包/版本,再 best-effort 删对象(对象删除失败不阻塞 DB 删除)。"""
    packages = SkillPackageRepository(session)
    versions = SkillVersionRepository(session)
    package = await packages.require_for_tenant(tenant_id, package_id)
    rows = await versions.list_for_package(tenant_id, package.id)
    object_keys = [row.object_key for row in rows]
    for row in rows:
        await versions.delete(row)
    await packages.delete(package)
    if workspace is None:
        return
    for key in object_keys:
        try:
            await workspace.delete_object(tenant_id, key)
        except Exception as exc:  # noqa: BLE001 - best-effort:DB 已删,留日志
            logger.warning(
                "skill package object delete failed",
                tenant_id=tenant_id,
                object_key=key,
                error=f"{type(exc).__name__}: {exc}",
            )


async def resolve_skill(
    session: AsyncSession,
    tenant_id: str,
    skill_id: str,
    registry: SkillRegistry,
) -> SkillDefinition | None:
    """解析 run 要绑定的技能:内置优先,其次租户上传包的最新版本。"""
    builtin = registry.get(skill_id)
    if builtin is not None:
        return SkillDefinition(manifest=builtin)
    package = await SkillPackageRepository(session).find_by_slug(tenant_id, skill_id)
    if package is None or package.status != ACTIVE:
        return None
    version = await SkillVersionRepository(session).latest_for_package(
        tenant_id, package.id
    )
    if version is None:
        return None
    return _definition(package, version)


async def list_manifests(
    session: AsyncSession, tenant_id: str, registry: SkillRegistry
) -> list[SkillManifest]:
    """GET /skills:内置 manifest + 每个上传包的最新版本(按 id 排序)。"""
    manifests = list(registry.list())
    packages = await SkillPackageRepository(session).list_for_tenant(tenant_id)
    versions = SkillVersionRepository(session)
    for package in packages:
        if package.status != ACTIVE:
            continue
        version = await versions.latest_for_package(tenant_id, package.id)
        if version is not None:
            manifests.append(_manifest_for(package, version))
    return sorted(manifests, key=lambda manifest: manifest.id)
