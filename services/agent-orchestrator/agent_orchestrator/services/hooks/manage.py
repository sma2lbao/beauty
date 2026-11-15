"""钩子管理面:目标地址校验与增删改查、密钥轮换。"""

from __future__ import annotations

import asyncio
import ipaddress
import secrets
from collections.abc import Sequence
from urllib.parse import urlparse

from common.errors import ConflictError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.hook import KIND_WEBHOOK, AccountHook
from agent_orchestrator.repositories import HookRepository
from agent_orchestrator.schemas.hook import (
    HookCreate,
    HookRead,
    HookSecretRead,
    HookUpdate,
)
from agent_orchestrator.services import net_guard

from .config import current_config
from .constants import LOGGER_NAME, SECRET_BYTES, InvalidHookTarget

logger = get_logger(LOGGER_NAME)
# ---------------------------------------------------------------- 账号 API


def _require_enabled(settings: Settings) -> None:
    if not settings.hooks_enabled:
        raise ConflictError("hooks.disabled", "应用级 hooks 未启用")


def _to_read(row: AccountHook) -> HookRead:
    return HookRead(
        id=row.id,
        event=row.event,
        matcher=row.matcher or "*",
        kind=row.kind or KIND_WEBHOOK,
        target=row.target,
        timeout_seconds=int(row.timeout_seconds or 3),
        enabled=bool(row.enabled),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_secret_read(row: AccountHook) -> HookSecretRead:
    return HookSecretRead(**_to_read(row).model_dump(), secret=row.secret)


def _new_secret() -> str:
    return secrets.token_urlsafe(SECRET_BYTES)


def _literal_address(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


async def _assert_target_address(url: str, *, allowed_hosts: Sequence[str]) -> None:
    """严格出站校验(§9):字面 IP 直判,域名必须能解析且不得命中内网。

    与 net_guard.assert_url_allowed 的差别只在"解析失败**拒绝**":hook 是账号
    主动配置的固定目标,DNS 失败说明配置不可用;请求期仍用 assert 的 fail-open
    口径兜底(避免运行中 DNS 抖动变成拒绝执行)。
    """
    host = net_guard.url_host(url)
    if not host:
        raise InvalidHookTarget("hooks.invalid_target", "hook target 缺少主机名")
    if host in net_guard.parse_allowed_hosts(allowed_hosts):
        return
    literal = _literal_address(host)
    if literal is not None:
        if net_guard.is_blocked_address(literal):
            raise InvalidHookTarget(
                "hooks.invalid_target", f"hook target 指向内网,已拒绝:{host}"
            )
        return
    if host in net_guard.LOCALHOST_NAMES:
        raise InvalidHookTarget(
            "hooks.invalid_target", f"hook target 指向内网,已拒绝:{host}"
        )
    resolver = current_config().resolver
    try:
        addresses = (
            list(resolver(host))
            if resolver is not None
            else await asyncio.to_thread(net_guard.resolve_host_sync, host)
        )
    except Exception as exc:  # noqa: BLE001 - 解析失败即按"目标不可用"拒绝
        raise InvalidHookTarget(
            "hooks.invalid_target", f"hook target 主机无法解析:{host}"
        ) from exc
    if not addresses:
        raise InvalidHookTarget(
            "hooks.invalid_target", f"hook target 主机无法解析:{host}"
        )
    blocked = [
        address for address in addresses if net_guard.is_blocked_address(address)
    ]
    if blocked:
        raise InvalidHookTarget(
            "hooks.invalid_target",
            f"hook target 指向内网,已拒绝:{host}(命中 {', '.join(blocked)})",
        )


async def _validate_target(target: str, *, settings: Settings) -> str:
    """创建/更新 target 时校验 scheme + 严格地址(§7/§9);返回 strip 后的 URL。"""
    url = target.strip()
    parsed = urlparse(url)
    schemes = ("https", "http") if settings.hooks_allow_http else ("https",)
    if parsed.scheme not in schemes or not parsed.hostname:
        expected = "https" if not settings.hooks_allow_http else "http(s)"
        raise InvalidHookTarget(
            "hooks.invalid_target", f"hook target 必须是 {expected} 地址"
        )
    await _assert_target_address(
        url, allowed_hosts=net_guard.parse_allowed_hosts(settings.mcp_allowed_hosts)
    )
    return url


async def list_hooks(
    session: AsyncSession,
    account_id: str,
    *,
    event: str | None = None,
    settings: Settings,
) -> list[HookRead]:
    _require_enabled(settings)
    rows = await HookRepository(session).list_for_account(account_id, event=event)
    return [_to_read(row) for row in rows]


async def create_hook(
    session: AsyncSession,
    account_id: str,
    data: HookCreate,
    *,
    settings: Settings,
) -> HookSecretRead:
    _require_enabled(settings)
    repo = HookRepository(session)
    existing = await repo.count_for_account(account_id)
    if existing >= max(1, settings.hooks_max_per_account):
        raise ConflictError(
            "hooks.too_many",
            f"单账号最多 {settings.hooks_max_per_account} 条 hook",
        )
    row = AccountHook(
        account_id=account_id,
        event=data.event,
        matcher=(data.matcher or "*").strip() or "*",
        kind=KIND_WEBHOOK,
        target=await _validate_target(data.target, settings=settings),
        secret=_new_secret(),
        timeout_seconds=data.timeout_seconds,
        enabled=data.enabled,
    )
    await repo.add(row)
    logger.info(
        "hook.created", hook_id=row.id, account_id=account_id, hook_event=row.event
    )
    return _to_secret_read(row)


async def update_hook(
    session: AsyncSession,
    account_id: str,
    hook_id: str,
    data: HookUpdate,
    *,
    settings: Settings,
) -> HookRead:
    _require_enabled(settings)
    row = await HookRepository(session).require_for_account(account_id, hook_id)
    if data.matcher is not None:
        row.matcher = data.matcher.strip() or "*"
    if data.target is not None:
        row.target = await _validate_target(data.target, settings=settings)
    if data.timeout_seconds is not None:
        row.timeout_seconds = data.timeout_seconds
    if data.enabled is not None:
        row.enabled = data.enabled
    await session.flush()
    return _to_read(row)


async def delete_hook(
    session: AsyncSession, account_id: str, hook_id: str, *, settings: Settings
) -> None:
    _require_enabled(settings)
    repo = HookRepository(session)
    row = await repo.require_for_account(account_id, hook_id)
    await repo.delete(row)
    # 删除不入 audit_logs(§12:量大且非管理动作),只留结构化日志。
    logger.warning("hook.deleted", hook_id=hook_id, account_id=account_id)


async def rotate_secret(
    session: AsyncSession, account_id: str, hook_id: str, *, settings: Settings
) -> HookSecretRead:
    _require_enabled(settings)
    row = await HookRepository(session).require_for_account(account_id, hook_id)
    row.secret = _new_secret()
    await session.flush()
    logger.info("hook.secret_rotated", hook_id=hook_id, account_id=account_id)
    return _to_secret_read(row)
