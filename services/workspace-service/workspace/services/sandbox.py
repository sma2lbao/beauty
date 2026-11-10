"""沙箱业务逻辑:DB 生命周期 + provider 编排(§7 阶段 B / P1)。

约定:

- 先落库(status=creating)再创建运行时,失败也保留 failed 记录可追踪;
- "过期/创建失败"这类事实性状态在抛错前显式 commit——否则统一异常处理会让
  `session_scope` 回滚,痕迹就丢了(本层唯一的提交例外);
- provider 异常统一收口成 `UpstreamError`,不把 SDK 堆栈泄漏给 API。

W5 行数治理:内部生命周期辅助函数移到 `workspace.services.sandbox_lifecycle`,
本模块仍是唯一公开门面并重导出全部原有名字(含下划线开头的模块级工具),
`from workspace.services.sandbox import X` 与 `sandbox_service.xxx` 的用法不变。
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Awaitable, Callable
from datetime import timedelta

import structlog
from common.errors import AppError, ConflictError, UpstreamError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workspace.core.config import Settings
from workspace.models.sandbox import SandboxInstance
from workspace.repositories import SandboxRepository
from workspace.schemas.sandbox import (
    MAX_TREE_DEPTH,
    MAX_TREE_ENTRIES,
    SandboxCreate,
    SandboxExecRequest,
    SandboxFileWrite,
    ensure_workspace_path,
)
from workspace.services.sandbox_lifecycle import (
    ENSURE_ATTEMPTS,
    ERROR_MAX_LEN,
    NAME_HEX_LEN,
    NAME_PREFIX,
    _as_utc,
    _claim_reapable,
    _claim_rebuild,
    _destroy_quietly,
    _ensure_usable,
    _error_text,
    _find_by_scope,
    _insert_ensured,
    _mark_failed,
    _new_name,
    _new_sandbox,
    _now,
    _renew,
    _spec_from,
    _start_runtime,
    _ttl_seconds,
    _write_back_reaped,
)
from workspace.services.sandbox_provider import (
    ExecResult,
    FileEntry,
    SandboxProvider,
    SandboxSpec,
)

logger = structlog.get_logger(__name__)

__all__ = [
    # 常量(拆分前在 sandbox 模块顶层)
    "ENSURE_ATTEMPTS",
    "ERROR_MAX_LEN",
    "NAME_HEX_LEN",
    "NAME_PREFIX",
    # 公开 API
    "create_sandbox",
    "destroy_sandbox",
    "ensure_sandbox",
    "exec_sandbox",
    "list_sandboxes",
    "list_tree",
    "read_file",
    "reap_expired",
    "renew_sandbox",
    "require_sandbox",
    "require_usable_sandbox",
    "touch_sandbox",
    "write_file",
    # provider 契约(原模块命名空间里就有这些名字)
    "ExecResult",
    "FileEntry",
    "SandboxProvider",
    "SandboxSpec",
    # 拆分前在本模块定义的内部工具,保持可导入
    "_as_utc",
    "_claim_rebuild",
    "_destroy_quietly",
    "_ensure_usable",
    "_error_text",
    "_find_by_scope",
    "_insert_ensured",
    "_mark_failed",
    "_new_name",
    "_new_sandbox",
    "_now",
    "_renew",
    "_spec_from",
    "_start_runtime",
    "_ttl_seconds",
]


async def create_sandbox(
    session: AsyncSession,
    account_id: str,
    data: SandboxCreate,
    provider: SandboxProvider,
    settings: Settings,
) -> SandboxInstance:
    """创建沙箱:先落库(creating) → 调 provider → 成功置 running。

    失败时保留 failed 记录(含 last_error)再抛错,便于运维追查。
    """
    record = _new_sandbox(account_id, data, provider, settings)
    await SandboxRepository(session).add(record)
    await _start_runtime(session, record, provider, settings)
    return record


async def ensure_sandbox(
    session: AsyncSession,
    account_id: str,
    *,
    scope_key: str,
    spec: SandboxCreate,
    provider: SandboxProvider,
    settings: Settings,
) -> tuple[SandboxInstance, bool]:
    """按 (account_id, scope_key) 幂等 get-or-create(设计 §11.2 / P0-05)。

    返回 (sandbox, created):created=True 表示本次新建/重建了运行时。
    未命中→插入(并发由唯一约束兜底:IntegrityError → rollback → 重查);
    命中 running 未过期→续期复用;命中 stopped/failed/已过期→原地重建。
    IntegrityError 不会抛给调用方。
    """
    for _ in range(ENSURE_ATTEMPTS):
        record = await _find_by_scope(session, account_id, scope_key)
        if record is None:
            inserted = await _insert_ensured(
                session, account_id, scope_key, spec, provider, settings
            )
            if inserted is not None:
                return inserted, True
            continue
        expired_running = False
        if record.status == "running":
            if _as_utc(record.expires_at) > _now():
                _renew(record, settings)
                return record, False
            expired_running = True
        elif record.status == "creating":
            raise ConflictError(
                "sandbox.creating",
                "沙箱仍在创建中,请稍后重试",
                details={"sandbox_id": record.id, "scope_key": scope_key},
            )
        stale_name = record.external_name
        if await _claim_rebuild(session, record, spec, provider, settings):
            if expired_running:
                # 回收放在抢到行之后,并发下只有一个副本销毁旧运行时
                await _destroy_quietly(provider, stale_name)
            await _start_runtime(session, record, provider, settings)
            return record, True
        await session.rollback()
    raise ConflictError(
        "sandbox.ensure_busy",
        "沙箱正在被并发创建,请稍后重试",
        details={"scope_key": scope_key},
    )


async def list_sandboxes(
    session: AsyncSession, account_id: str, *, scope_key: str | None = None
) -> list[SandboxInstance]:
    """列本账号沙箱(新→旧);scope_key 精确匹配,用于按会话定位"执行现场"。"""
    criteria = () if scope_key is None else (SandboxInstance.scope_key == scope_key,)
    return await SandboxRepository(session).list_scoped(
        account_id,
        *criteria,
        order_by=[SandboxInstance.created_at.desc(), SandboxInstance.id.desc()],
    )


async def require_sandbox(
    session: AsyncSession, account_id: str, sandbox_id: str
) -> SandboxInstance:
    return await SandboxRepository(session).require_for_account(account_id, sandbox_id)


async def require_usable_sandbox(
    session: AsyncSession,
    account_id: str,
    sandbox_id: str,
    provider: SandboxProvider,
) -> SandboxInstance:
    """取本账号沙箱并做运行前检查(404/409 语义与 exec 一致)。

    shell 会话等长生命周期操作复用这一入口:跨账号仍是 404,过期/停止仍是 409。
    """
    record = await require_sandbox(session, account_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    return record


def renew_sandbox(record: SandboxInstance, settings: Settings) -> None:
    """滑动续期(不查库):shell 会话有输入/连流时由调用方续一次。"""
    _renew(record, settings)


async def touch_sandbox(
    session: AsyncSession,
    account_id: str,
    sandbox_id: str,
    provider: SandboxProvider,
    settings: Settings,
) -> SandboxInstance:
    """显式续期(滑动 TTL)。"""
    record = await require_sandbox(session, account_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    _renew(record, settings)
    return record


async def destroy_sandbox(
    session: AsyncSession,
    account_id: str,
    sandbox_id: str,
    provider: SandboxProvider,
) -> None:
    """删除沙箱:先销毁运行时再删记录;provider 失败保留记录并 502。"""
    record = await require_sandbox(session, account_id, sandbox_id)
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
    account_id: str,
    sandbox_id: str,
    data: SandboxExecRequest,
    provider: SandboxProvider,
    settings: Settings,
) -> ExecResult:
    """在沙箱内执行命令;成功后滑动续期。"""
    record = await require_sandbox(session, account_id, sandbox_id)
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
    account_id: str,
    sandbox_id: str,
    data: SandboxFileWrite,
    provider: SandboxProvider,
) -> None:
    """写文件:路径必须落在 /workspace 下(Base64 传输)。"""
    record = await require_sandbox(session, account_id, sandbox_id)
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
    account_id: str,
    sandbox_id: str,
    path: str,
    provider: SandboxProvider,
) -> bytes:
    """读文件:路径必须落在 /workspace 下;不存在由 provider 抛 404。"""
    record = await require_sandbox(session, account_id, sandbox_id)
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


async def list_tree(
    session: AsyncSession,
    account_id: str,
    sandbox_id: str,
    path: str,
    depth: int,
    provider: SandboxProvider,
) -> tuple[list[FileEntry], bool]:
    """列沙箱内目录树:路径必须落在 /workspace 下;超条目上限截断并标记。

    返回 (entries, truncated);depth 在这里再夹一次(防御调用方绕过 Query 校验)。
    """
    record = await require_sandbox(session, account_id, sandbox_id)
    await _ensure_usable(session, record, provider)
    safe_path = ensure_workspace_path(path)
    bounded = max(1, min(depth, MAX_TREE_DEPTH))
    try:
        entries = await provider.list_dir(
            record.external_name, safe_path, depth=bounded
        )
    except AppError:
        raise
    except Exception as exc:
        raise UpstreamError(
            "sandbox.provider_error",
            "列举沙箱目录失败",
            details={"sandbox_id": record.id, "path": safe_path},
        ) from exc
    truncated = len(entries) > MAX_TREE_ENTRIES
    if truncated:
        return list(entries[:MAX_TREE_ENTRIES]), True
    return list(entries), False


async def reap_expired(
    factory: async_sessionmaker[AsyncSession],
    provider: SandboxProvider,
    settings: Settings,
    *,
    limit: int = 100,
    on_reaped: Callable[[str, str], Awaitable[None]] | None = None,
) -> int:
    """回收过期沙箱(多副本互斥):短事务领取 → 事务外销毁 → 短事务回写。

    领取租约语义(P0-4c):

    - 领取在短事务内完成:命中行的 `expires_at` 立即推后到 now +
      `sandbox_reap_lease_seconds`(默认 300s)。提交后其它副本的
      claim/list 都看不到这批行,因此同一批运行时只会被销毁一次;
    - `provider.destroy` 一律在**事务外**执行,不在数据库事务里做外部调用;
    - 成功:status=stopped、last_error=None;失败:保留原状态(不写 status),
      `last_error` 留痕,并把 `expires_at` 推后到 now +
      `sandbox_reap_retry_delay_seconds`(默认 30s),避免每轮锤同一个坏容器;
    - 领取后进程崩溃:租约到期后该行重新变成"过期",下一轮会被重新领取——
      destroy 必须按幂等对待(provider 侧失败也已被异常兜底)。

    返回本轮成功回收的数量。记录保留(status=stopped)以便审计;
    显式 DELETE 才真正删行。

    `on_reaped(account_id, sandbox_id)` 是可选的联动回调(进程装配时传
    shell 会话管理器的 close_sandbox):运行时被回收后,该沙箱上的 shell
    会话也一并关闭,避免内存里留下"永远等不到 EOF"的会话。回调在回写提交
    之后逐条执行,best-effort(异常只记 warning)。
    """
    now = _now()
    lease_seconds = settings.sandbox_reap_lease_seconds
    claimed = await _claim_reapable(
        factory, now=now, limit=limit, lease_seconds=lease_seconds
    )
    if not claimed:
        return 0

    succeeded: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for sandbox_id, account_id, sandbox_name in claimed:
        try:
            await provider.destroy(sandbox_name)
        except Exception as exc:
            error_text = _error_text(exc)
            failed.append((sandbox_id, error_text))
            logger.warning(
                "sandbox reap failed",
                sandbox_id=sandbox_id,
                sandbox_name=sandbox_name,
                error=error_text,
            )
            continue
        succeeded.append((sandbox_id, account_id))

    await _write_back_reaped(
        factory,
        succeeded=succeeded,
        failed=failed,
        retry_delay_seconds=settings.sandbox_reap_retry_delay_seconds,
        claimed_until=now + timedelta(seconds=lease_seconds),
    )

    if on_reaped is not None:
        for sandbox_id, account_id in succeeded:
            try:
                await on_reaped(account_id, sandbox_id)
            except Exception as exc:  # noqa: BLE001 - 清理失败不影响回收结果
                logger.warning(
                    "sandbox reap callback failed",
                    sandbox_id=sandbox_id,
                    error=_error_text(exc),
                )
    return len(succeeded)
