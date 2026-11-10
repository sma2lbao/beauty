"""沙箱生命周期的内部实现:时间/TTL、落库行构造、运行时拉起与幂等 get-or-create。

W5 行数治理从 `sandbox.py` 拆出的纯内部模块;对外仍是
`workspace.services.sandbox` 门面,本模块不构成新的公开 API。
logger 名沿用拆分前的 `workspace.services.sandbox`,日志字段保持不变。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import structlog
from common.errors import AppError, ConflictError, UpstreamError
from database.session import session_scope
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workspace.core.config import Settings
from workspace.models.sandbox import SandboxInstance
from workspace.repositories import CrossAccountSandboxRepository, SandboxRepository
from workspace.schemas.sandbox import SandboxCreate
from workspace.services.sandbox_provider import SandboxProvider, SandboxSpec

# 拆分前就在 sandbox 模块顶层的日志器:名字显式钉住,不随本模块改名
logger = structlog.get_logger("workspace.services.sandbox")

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


ENSURE_ATTEMPTS = 3


def _new_name() -> str:
    return f"{NAME_PREFIX}{uuid4().hex[:NAME_HEX_LEN]}"


def _new_sandbox(
    account_id: str,
    data: SandboxCreate,
    provider: SandboxProvider,
    settings: Settings,
    *,
    scope_key: str | None = None,
) -> SandboxInstance:
    """按请求 + 配置构造待落库的沙箱行(status=creating)。"""
    now = _now()
    requested_ttl = (
        data.ttl_seconds
        if data.ttl_seconds is not None
        else settings.sandbox_default_ttl_seconds
    )
    ttl = max(1, min(requested_ttl, settings.sandbox_max_ttl_seconds))
    name = _new_name()
    cpus = data.cpus if data.cpus is not None else settings.sandbox_default_cpus
    memory_mb = (
        data.memory_mb
        if data.memory_mb is not None
        else settings.sandbox_default_memory_mb
    )
    return SandboxInstance(
        id=uuid4().hex,
        account_id=account_id,
        name=name,
        scope_key=scope_key,
        kind=data.kind,
        provider=provider.provider,
        external_name=name,
        image=data.image or settings.sandbox_default_image,
        cpus=cpus,
        memory_mb=memory_mb,
        network=data.network or settings.sandbox_network,
        workspace_id=data.workspace_id,
        run_id=data.run_id,
        status="creating",
        expires_at=now + timedelta(seconds=ttl),
        last_used_at=now,
        meta={"ttl_seconds": ttl},
    )


def _spec_from(record: SandboxInstance, settings: Settings) -> SandboxSpec:
    """把已落库的行翻译成 provider 需要的运行时规格。"""
    return SandboxSpec(
        account_id=record.account_id,
        name=record.external_name,
        kind=record.kind,
        image=record.image,
        cpus=record.cpus,
        memory_mb=record.memory_mb,
        network=record.network,
        ttl_seconds=_ttl_seconds(record, settings),
    )


async def _start_runtime(
    session: AsyncSession,
    record: SandboxInstance,
    provider: SandboxProvider,
    settings: Settings,
) -> None:
    """拉起运行时;失败保留 failed 记录(含 last_error)再抛错。"""
    spec = _spec_from(record, settings)
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
            details={"sandbox_id": record.id, "sandbox_name": spec.name},
        ) from exc
    record.status = "running"
    record.last_error = None
    record.last_used_at = _now()
    record.expires_at = record.last_used_at + timedelta(
        seconds=_ttl_seconds(record, settings)
    )


async def _find_by_scope(
    session: AsyncSession, account_id: str, scope_key: str
) -> SandboxInstance | None:
    statement = SandboxRepository(session).scoped(
        account_id, SandboxInstance.scope_key == scope_key
    )
    return await session.scalar(statement)


async def _insert_ensured(
    session: AsyncSession,
    account_id: str,
    scope_key: str,
    data: SandboxCreate,
    provider: SandboxProvider,
    settings: Settings,
) -> SandboxInstance | None:
    """插入带 scope_key 的新行;并发撞唯一约束时 rollback 并返回 None。"""
    record = _new_sandbox(account_id, data, provider, settings, scope_key=scope_key)
    try:
        await SandboxRepository(session).add(record)
    except IntegrityError:
        # 另一个副本已插入同一 (account_id, scope_key):回滚后由调用方重查
        await session.rollback()
        return None
    await _start_runtime(session, record, provider, settings)
    return record


async def _claim_rebuild(
    session: AsyncSession,
    record: SandboxInstance,
    data: SandboxCreate,
    provider: SandboxProvider,
    settings: Settings,
) -> bool:
    """抢占式原地重建:条件 UPDATE 只有一方成功,避免多副本重复拉起运行时。

    WHERE 带上旧 status/name,若行已被别的副本改掉则 rowcount=0;
    调用方 rollback 后重新查询(此时通常已是 running)。
    """
    candidate = _new_sandbox(
        record.account_id, data, provider, settings, scope_key=record.scope_key
    )
    values: dict[str, object] = {
        field: getattr(candidate, field)
        for field in (
            "name",
            "scope_key",
            "kind",
            "provider",
            "external_name",
            "image",
            "cpus",
            "memory_mb",
            "network",
            "workspace_id",
            "run_id",
            "expires_at",
            "last_used_at",
            "meta",
        )
    }
    values.update({"status": "creating", "last_error": None})
    result = await session.execute(
        update(SandboxInstance)
        .where(
            SandboxInstance.id == record.id,
            SandboxInstance.status == record.status,
            SandboxInstance.name == record.name,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    await session.refresh(record)
    return True


async def _claim_reapable(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int,
    lease_seconds: int,
) -> list[tuple[str, str, str]]:
    """短事务原子领取过期沙箱,返回 (id, account_id, external_name) 快照。

    领取即在同事务内把 expires_at 推后到租约末端并提交:此后其它副本的
    claim/list 都看不到这批行,回收只在事务外做一次。
    """
    async with session_scope(factory) as session:
        records = await CrossAccountSandboxRepository(session).claim_expired(
            now, limit=limit, lease_seconds=lease_seconds
        )
        return [
            (record.id, record.account_id, record.external_name) for record in records
        ]


async def _write_back_reaped(
    factory: async_sessionmaker[AsyncSession],
    *,
    succeeded: list[tuple[str, str]],
    failed: list[tuple[str, str]],
    retry_delay_seconds: int,
    claimed_until: datetime,
) -> None:
    """短事务回写回收结果:成功置 stopped,失败留痕并推后 expires_at。

    失败行只在仍处于本轮租约窗口(expires_at <= claimed_until)时才推后到
    now + retry_delay,避免覆盖用户刚做的续期。事务内不做任何外部调用;
    行已被并发删除时跳过回写。
    """
    sandbox_ids = [sandbox_id for sandbox_id, _ in succeeded] + [
        sandbox_id for sandbox_id, _ in failed
    ]
    if not sandbox_ids:
        return
    retry_at = _now() + timedelta(seconds=retry_delay_seconds)
    async with session_scope(factory) as session:
        records = {
            record.id: record
            for record in await session.scalars(
                select(SandboxInstance).where(SandboxInstance.id.in_(sandbox_ids))
            )
        }
        for sandbox_id, _account_id in succeeded:
            record = records.get(sandbox_id)
            if record is None:
                continue
            record.status = "stopped"
            record.last_error = None
        for sandbox_id, error_text in failed:
            record = records.get(sandbox_id)
            if record is None:
                continue
            record.last_error = error_text
            if _as_utc(record.expires_at) <= claimed_until:
                record.expires_at = retry_at


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
