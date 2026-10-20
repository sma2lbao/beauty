"""沙箱业务逻辑:DB 生命周期 + provider 编排(§7 阶段 B / P1)。

约定:

- 先落库(status=creating)再创建运行时,失败也保留 failed 记录可追踪;
- "过期/创建失败"这类事实性状态在抛错前显式 commit——否则统一异常处理会让
  `session_scope` 回滚,痕迹就丢了(本层唯一的提交例外);
- provider 异常统一收口成 `UpstreamError`,不把 SDK 堆栈泄漏给 API。
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import structlog
from common.errors import AppError, ConflictError, UpstreamError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workspace.core.config import Settings
from workspace.models.sandbox import SandboxInstance
from workspace.repositories import CrossTenantSandboxRepository, SandboxRepository
from workspace.schemas.sandbox import (
    SandboxCreate,
    SandboxExecRequest,
    SandboxFileWrite,
    ensure_workspace_path,
)
from workspace.services.sandbox_provider import (
    ExecResult,
    SandboxProvider,
    SandboxSpec,
)

logger = structlog.get_logger(__name__)

NAME_PREFIX = "bm-"
NAME_HEX_LEN = 20
ERROR_MAX_LEN = 500


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """sqlite 的 DateTime 会丢 tzinfo(时间按 UTC 存),比较前补回 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        text = f"{exc.code}: {exc.message}"
    else:
        text = f"{type(exc).__name__}: {exc}"
    return text[:ERROR_MAX_LEN]


def _ttl_seconds(record: SandboxInstance, settings: Settings) -> int:
    """实例实际生效的 TTL:创建时记在 meta,续期时再按配置上限收紧。"""
    meta = record.meta or {}
    raw = meta.get("ttl_seconds")
    ttl = int(raw) if isinstance(raw, int) else settings.sandbox_default_ttl_seconds
    return max(1, min(ttl, settings.sandbox_max_ttl_seconds))


def _renew(record: SandboxInstance, settings: Settings) -> None:
    """滑动 TTL:重置 last_used_at,expires_at = now + min(ttl, max_ttl)。"""
    now = _now()
    record.last_used_at = now
    record.expires_at = now + timedelta(seconds=_ttl_seconds(record, settings))


async def _destroy_quietly(provider: SandboxProvider, name: str) -> None:
    try:
        await provider.destroy(name)
    except Exception as exc:
        # 回收失败不改变"已过期/已停止"的对外结论,只留痕供排查
        logger.warning(
            "sandbox destroy failed during cleanup",
            sandbox_name=name,
            error=_error_text(exc),
        )


async def _mark_failed(
    session: AsyncSession, record: SandboxInstance, exc: BaseException
) -> None:
    record.status = "failed"
    record.last_error = _error_text(exc)
    # 失败记录要保留:请求级 session_scope 会因抛错回滚,这里先提交
    await session.commit()


async def create_sandbox(
    session: AsyncSession,
    tenant_id: str,
    data: SandboxCreate,
    provider: SandboxProvider,
    settings: Settings,
) -> SandboxInstance:
    """创建沙箱:先落库(creating) → 调 provider → 成功置 running。

    失败时保留 failed 记录(含 last_error)再抛错,便于运维追查。
    """
    now = _now()
    requested_ttl = (
        data.ttl_seconds
        if data.ttl_seconds is not None
        else settings.sandbox_default_ttl_seconds
    )
    ttl = max(1, min(requested_ttl, settings.sandbox_max_ttl_seconds))
    cpus = data.cpus if data.cpus is not None else settings.sandbox_default_cpus
    memory_mb = (
        data.memory_mb
        if data.memory_mb is not None
        else settings.sandbox_default_memory_mb
    )
    network = data.network or settings.sandbox_network
    name = f"{NAME_PREFIX}{uuid4().hex[:NAME_HEX_LEN]}"
    record = SandboxInstance(
        id=uuid4().hex,
        tenant_id=tenant_id,
        name=name,
        kind=data.kind,
        provider=provider.provider,
        external_name=name,
        image=data.image or settings.sandbox_default_image,
        cpus=cpus,
        memory_mb=memory_mb,
        network=network,
        workspace_id=data.workspace_id,
        run_id=data.run_id,
        status="creating",
        expires_at=now + timedelta(seconds=ttl),
        last_used_at=now,
        meta={"ttl_seconds": ttl},
    )
    await SandboxRepository(session).add(record)
    spec = SandboxSpec(
        tenant_id=tenant_id,
        name=name,
        kind=data.kind,
        image=record.image,
        cpus=cpus,
        memory_mb=memory_mb,
        network=network,
        ttl_seconds=ttl,
    )
    try:
        await provider.create(spec)
    except AppError as exc:
        await _mark_failed(session, record, exc)
        raise
    except Exception as exc:
        await _mark_failed(session, record, exc)
        raise UpstreamError(
            "sandbox.provider_error",
            "沙箱运行时创建失败",
            details={"sandbox_id": record.id, "sandbox_name": name},
        ) from exc
    record.status = "running"
    record.last_error = None
    record.last_used_at = _now()
    record.expires_at = record.last_used_at + timedelta(seconds=ttl)
    return record


async def list_sandboxes(
    session: AsyncSession, tenant_id: str
) -> list[SandboxInstance]:
    return await SandboxRepository(session).list_scoped(
        tenant_id,
        order_by=[SandboxInstance.created_at.desc(), SandboxInstance.id.desc()],
    )


async def require_sandbox(
    session: AsyncSession, tenant_id: str, sandbox_id: str
) -> SandboxInstance:
    return await SandboxRepository(session).require_for_tenant(tenant_id, sandbox_id)


async def _ensure_usable(
    session: AsyncSession,
    record: SandboxInstance,
    provider: SandboxProvider,
) -> None:
    """运行前检查:状态必须是 running,且未过期;过期先回收再 409。"""
    if record.status != "running":
        if record.status == "creating":
            raise ConflictError(
                "sandbox.creating",
                "沙箱仍在创建中,请稍后重试",
                details={"sandbox_id": record.id},
            )
        if record.status == "failed":
            raise ConflictError(
                "sandbox.failed",
                "沙箱创建失败,请重新创建",
                details={"sandbox_id": record.id, "last_error": record.last_error},
            )
        raise ConflictError(
            "sandbox.stopped",
            "沙箱已停止,请重新创建",
            details={"sandbox_id": record.id},
        )
    if _as_utc(record.expires_at) > _now():
        return
    record.status = "stopped"
    record.last_error = "沙箱已过期,已触发回收"
    await session.commit()
    await _destroy_quietly(provider, record.external_name)
    raise ConflictError(
        "sandbox.expired",
        "沙箱已过期,请重新创建",
        details={"sandbox_id": record.id},
    )


async def touch_sandbox(
    session: AsyncSession,
    tenant_id: str,
    sandbox_id: str,
    provider: SandboxProvider,
    settings: Settings,
) -> SandboxInstance:
    """显式续期(滑动 TTL)。"""
    record = await require_sandbox(session, tenant_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    _renew(record, settings)
    return record


async def destroy_sandbox(
    session: AsyncSession,
    tenant_id: str,
    sandbox_id: str,
    provider: SandboxProvider,
) -> None:
    """删除沙箱:先销毁运行时再删记录;provider 失败保留记录并 502。"""
    record = await require_sandbox(session, tenant_id, sandbox_id)
    try:
        await provider.destroy(record.external_name)
    except AppError as exc:
        record.last_error = _error_text(exc)
        await session.commit()
        raise
    except Exception as exc:
        record.last_error = _error_text(exc)
        await session.commit()
        raise UpstreamError(
            "sandbox.provider_error",
            "销毁沙箱失败",
            details={"sandbox_id": record.id},
        ) from exc
    await SandboxRepository(session).delete(record)


async def exec_sandbox(
    session: AsyncSession,
    tenant_id: str,
    sandbox_id: str,
    data: SandboxExecRequest,
    provider: SandboxProvider,
    settings: Settings,
) -> ExecResult:
    """在沙箱内执行命令;成功后滑动续期。"""
    record = await require_sandbox(session, tenant_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    timeout = min(
        data.timeout_seconds
        if data.timeout_seconds is not None
        else settings.sandbox_exec_timeout_seconds,
        settings.sandbox_exec_timeout_seconds,
    )
    try:
        result = await provider.exec(
            record.external_name,
            list(data.argv),
            cwd=data.cwd,
            env=dict(data.env),
            stdin=data.stdin,
            timeout_seconds=timeout,
        )
    except AppError:
        raise
    except Exception as exc:
        raise UpstreamError(
            "sandbox.provider_error",
            "沙箱执行命令失败",
            details={"sandbox_id": record.id},
        ) from exc
    _renew(record, settings)
    return result


async def write_file(
    session: AsyncSession,
    tenant_id: str,
    sandbox_id: str,
    data: SandboxFileWrite,
    provider: SandboxProvider,
) -> None:
    """写文件:路径必须落在 /workspace 下(Base64 传输)。"""
    record = await require_sandbox(session, tenant_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    path = ensure_workspace_path(data.path)
    try:
        payload = base64.b64decode(data.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(
            "sandbox.content_invalid", "content_base64 不是合法 Base64"
        ) from exc
    try:
        await provider.write_file(record.external_name, path, payload)
    except AppError:
        raise
    except Exception as exc:
        raise UpstreamError(
            "sandbox.provider_error",
            "写入沙箱文件失败",
            details={"sandbox_id": record.id, "path": path},
        ) from exc


async def read_file(
    session: AsyncSession,
    tenant_id: str,
    sandbox_id: str,
    path: str,
    provider: SandboxProvider,
) -> bytes:
    """读文件:路径必须落在 /workspace 下;不存在由 provider 抛 404。"""
    record = await require_sandbox(session, tenant_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    safe_path = ensure_workspace_path(path)
    try:
        return await provider.read_file(record.external_name, safe_path)
    except AppError:
        raise
    except Exception as exc:
        raise UpstreamError(
            "sandbox.provider_error",
            "读取沙箱文件失败",
            details={"sandbox_id": record.id, "path": safe_path},
        ) from exc


async def reap_expired(
    factory: async_sessionmaker[AsyncSession],
    provider: SandboxProvider,
    *,
    limit: int = 100,
) -> int:
    """回收过期沙箱:逐个销毁,失败只记 last_error,不阻塞其他实例。

    返回本轮成功回收的数量。记录保留(status=stopped)以便审计;
    显式 DELETE 才真正删行。
    """
    now = _now()
    reaped = 0
    async with session_scope(factory) as session:
        records = await CrossTenantSandboxRepository(session).list_expired(
            now, limit=limit
        )
        for record in records:
            try:
                await provider.destroy(record.external_name)
            except Exception as exc:
                record.last_error = _error_text(exc)
                logger.warning(
                    "sandbox reap failed",
                    sandbox_id=record.id,
                    sandbox_name=record.external_name,
                    error=_error_text(exc),
                )
                continue
            record.status = "stopped"
            record.last_error = None
            reaped += 1
    return reaped
