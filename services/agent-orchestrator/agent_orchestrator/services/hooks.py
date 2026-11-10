"""应用级 hooks(P1-B):五个生命周期挂点 → 账号配置的出站 webhook。

契约见 docs/design/hooks.md。实现要点:

- **快照**:``load_hooks`` 读出账号 enabled hooks(按 created_at, id 升序),
  run 创建时写进 ``RunGraphState.hooks``——图节点不查库(依赖方向不变);
- **顺序**:PreToolUse 在审批策略之前(deny 短路,不产生 pending/确认),
  PostToolUse 在 offload 与审计 ``_record`` 之后(只追加模型可见 note);
- **失败语义**:超时/连接失败/非 2xx/响应过大默认放行(warning),
  ``hooks_fail_closed`` 才改 deny;PreCompact 的 skip 才能跳过压缩;
- **安全**:target 在创建/更新时严格校验(https + net_guard + DNS 必须可解析,
  与 MCP 的"解析失败放行"不同),每次请求前再走 net_guard 兜底;
  签名 = HMAC-SHA256(secret, timestamp + "." + body)(§4.1);
- **进程级配置**:仿 mcp_client.configure_url_guard——GraphPorts 是模块常量,
  因此 runner 在每次 run 时读进程级配置(容器装配调用 configure_hooks)。
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from common.errors import AppError, ConflictError
from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.graph.ports import (
    HOOK_EVENT_PRE_COMPACT,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_TOOL_EVENTS,
    HookDecision,
    HookOutcome,
)
from agent_orchestrator.models.hook import KIND_WEBHOOK, AccountHook
from agent_orchestrator.repositories import HookRepository
from agent_orchestrator.schemas.hook import (
    HookCreate,
    HookRead,
    HookSecretRead,
    HookUpdate,
)
from agent_orchestrator.services import net_guard, post_commit

logger = get_logger(__name__)

#: 请求体里 arguments/result 的截断长度(§4.1,尾部标注 "(truncated)")。
ARGUMENT_CHARS = 8000
#: 响应里 reason/note 的截断长度(§4.2);note 追加进模型可见内容前统一清洗。
REASON_CHARS = 512
NOTE_CHARS = 1000
#: 响应体上限(§4.3),超出丢弃并按失败处理(fail-open)。
RESPONSE_MAX_BYTES = 16 * 1024
#: 单条 hook 超时上限;实际取 min(本列, HOOKS_TIMEOUT_SECONDS, 30)。
MAX_TIMEOUT_SECONDS = 30.0
#: 协议头(§4.1)。
EVENT_HEADER = "X-DSH-Hook-Event"
ID_HEADER = "X-DSH-Hook-Id"
TIMESTAMP_HEADER = "X-DSH-Timestamp"
SIGNATURE_HEADER = "X-DSH-Signature"
#: secret 生成长度(token_urlsafe(32) ≈ 43 字符,列宽 128 够用)。
SECRET_BYTES = 32

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class InvalidHookTarget(AppError):
    """hook target 非法(非 https / 私网地址 / DNS 解析失败)→ 422。"""

    status = 422


@dataclass(frozen=True, slots=True)
class HookConfig:
    """进程级 hooks 配置(容器装配一次;runner 每次调用时读当前值)。"""

    enabled: bool = False
    timeout_seconds: float = 3.0
    fail_closed: bool = False
    max_per_account: int = 20
    allow_http: bool = False
    allowed_hosts: tuple[str, ...] = ()
    #: 测试注入:MockTransport 与假 resolver(生产为 None = 真实网络/DNS)。
    transport: httpx.AsyncBaseTransport | None = None
    resolver: net_guard.Resolver | None = None


_DEFAULT_CONFIG = HookConfig()
_CONFIG = _DEFAULT_CONFIG


def configure_hooks(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: net_guard.Resolver | None = None,
) -> None:
    """按部署配置装一次 hooks(容器装配调用;测试可注入 transport/resolver)。"""
    global _CONFIG
    _CONFIG = HookConfig(
        enabled=settings.hooks_enabled,
        timeout_seconds=float(settings.hooks_timeout_seconds),
        fail_closed=settings.hooks_fail_closed,
        max_per_account=int(settings.hooks_max_per_account),
        allow_http=settings.hooks_allow_http,
        allowed_hosts=net_guard.parse_allowed_hosts(settings.mcp_allowed_hosts),
        transport=transport,
        resolver=resolver,
    )


def reset_hooks() -> None:
    """恢复默认(关闭 + 真实网络);容器关闭与测试清理用。"""
    global _CONFIG
    _CONFIG = _DEFAULT_CONFIG


def current_config() -> HookConfig:
    """当前进程级配置(只读;测试用于断言装配结果)。"""
    return _CONFIG


# ---------------------------------------------------------------- 协议编解码


def _truncate(text: object, *, limit: int = ARGUMENT_CHARS) -> str:
    """截断到 limit 字符并在尾部标注(§4.1);None/非字符串统一成空串。"""
    value = "" if text is None else str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + "…(truncated)"


def _clean(text: object, *, limit: int) -> str:
    """去控制字符 + strip + 截断:响应字段回喂模型前统一清洗(§9)。"""
    if text is None:
        return ""
    return _CONTROL_CHARS.sub("", str(text)).strip()[:limit]


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """X-DSH-Signature:sha256=hex(hmac_sha256(secret, timestamp + "." + body))。"""
    message = timestamp.encode("utf-8") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def parse_response(raw: bytes, event: str) -> HookOutcome:
    """解析 hook 响应体最后一行 JSON(§4.2)。

    空体/非 JSON/未知 decision → allow;deny 只在 PreToolUse 生效、skip 只在
    PreCompact 生效,其余事件一律回落 allow(reason 仍透传供日志排查)。
    """
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return HookOutcome()
    try:
        data = json.loads(text.splitlines()[-1].strip())
    except ValueError:
        logger.debug("hook.response_not_json", hook_event=event)
        return HookOutcome()
    if not isinstance(data, dict):
        return HookOutcome()
    decision = str(data.get("decision") or "").strip().lower()
    reason = _clean(data.get("reason"), limit=REASON_CHARS)
    note = _clean(data.get("note"), limit=NOTE_CHARS)
    if decision == HookDecision.DENY.value and event == HOOK_EVENT_PRE_TOOL_USE:
        return HookOutcome(HookDecision.DENY, reason=reason, note=note)
    if decision == HookDecision.SKIP.value and event == HOOK_EVENT_PRE_COMPACT:
        return HookOutcome(HookDecision.SKIP, reason=reason, note=note)
    return HookOutcome(HookDecision.ALLOW, reason=reason, note=note)


def _join_notes(notes: Sequence[str]) -> str:
    if not notes:
        return ""
    return _clean("\n".join(notes), limit=NOTE_CHARS)


def _timeout_for(hook: Mapping[str, Any], config: HookConfig) -> float:
    try:
        configured = float(hook.get("timeout_seconds") or 3)
    except (TypeError, ValueError):
        configured = 3.0
    return max(0.1, min(configured, config.timeout_seconds, MAX_TIMEOUT_SECONDS))


def _matches(hook: Mapping[str, Any], event: str, tool: object) -> bool:
    """事件相等 + enabled + 工具事件再按 matcher(精确/glob)过滤。"""
    if str(hook.get("event") or "") != event:
        return False
    if not hook.get("enabled", True):
        return False
    if event not in HOOK_TOOL_EVENTS:
        return True
    if tool is None or not str(tool):
        return False
    matcher = str(hook.get("matcher") or "*")
    return matcher == "*" or fnmatch.fnmatchcase(str(tool), matcher)


async def _read_limited(response: httpx.Response) -> bytes | None:
    """读响应体,超 16 KiB 直接放弃(§4.3);None 表示超限。"""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > RESPONSE_MAX_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------- 执行器


class WebhookHookRunner:
    """按事件串行 POST 账号 hooks;首个 deny/skip 短路(§4.2)。

    默认实现 ``_NullHooks`` 在 graph/ports.py:未注入 runner 的单测/未配置场景
    零开销。本类无状态,进程级配置在每次 run 时读取。
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def run(
        self,
        hooks: Sequence[Mapping[str, Any]],
        event: str,
        payload: Mapping[str, Any],
    ) -> HookOutcome:
        config = _CONFIG
        if not config.enabled or not hooks:
            return HookOutcome()
        tool = payload.get("tool")
        selected = [hook for hook in hooks if _matches(hook, event, tool)]
        if not selected:
            return HookOutcome()
        notes: list[str] = []
        async with httpx.AsyncClient(
            transport=self._transport or config.transport,
            timeout=MAX_TIMEOUT_SECONDS,
        ) as client:
            for hook in selected:
                outcome = await self._call(client, config, hook, event, payload)
                if outcome is None:
                    # 失败:默认放行;fail_closed 收紧为 deny(只有 PreToolUse 生效)
                    if config.fail_closed:
                        return HookOutcome(
                            HookDecision.DENY,
                            reason="hook 调用失败(hooks_fail_closed)",
                        )
                    continue
                if outcome.note:
                    notes.append(outcome.note)
                if outcome.decision is not HookDecision.ALLOW:
                    return HookOutcome(
                        outcome.decision,
                        reason=outcome.reason,
                        note=_join_notes(notes),
                    )
        return HookOutcome(note=_join_notes(notes))

    async def _call(
        self,
        client: httpx.AsyncClient,
        config: HookConfig,
        hook: Mapping[str, Any],
        event: str,
        payload: Mapping[str, Any],
    ) -> HookOutcome | None:
        """单条 hook:净网校验 → 签名 POST → 限长解析;失败返回 None。"""
        target = str(hook.get("target") or "")
        hook_id = str(hook.get("id") or "")
        try:
            # §4.3/§9:请求前再走一次 net_guard(创建后 DNS 可能已经变了)。
            await net_guard.assert_url_allowed(
                target,
                allowed_hosts=config.allowed_hosts,
                resolver=config.resolver,
            )
        except net_guard.BlockedTargetError as exc:
            logger.warning(
                "hook.target_blocked",
                hook_id=hook_id,
                hook_event=event,
                error=str(exc),
            )
            return None
        body = json.dumps(
            {**payload, "event": event}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            EVENT_HEADER: event,
            ID_HEADER: hook_id,
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign(str(hook.get("secret") or ""), timestamp, body),
        }
        try:
            async with client.stream(
                "POST",
                target,
                content=body,
                headers=headers,
                timeout=_timeout_for(hook, config),
            ) as response:
                if not 200 <= response.status_code < 300:
                    logger.warning(
                        "hook.bad_status",
                        hook_id=hook_id,
                        hook_event=event,
                        status=response.status_code,
                    )
                    return None
                raw = await _read_limited(response)
        except httpx.HTTPError as exc:
            logger.warning(
                "hook.request_failed",
                hook_id=hook_id,
                hook_event=event,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        if raw is None:
            logger.warning("hook.response_too_large", hook_id=hook_id, hook_event=event)
            return None
        return parse_response(raw, event)


_DEFAULT_RUNNER = WebhookHookRunner()


# ---------------------------------------------------------------- 快照与调用


def _snapshot(row: AccountHook) -> dict[str, Any]:
    """ORM 行 → 图状态可序列化的 hook 快照(secret 必须带,签名要用)。"""
    return {
        "id": row.id,
        "account_id": row.account_id,
        "event": row.event,
        "matcher": row.matcher or "*",
        "kind": row.kind or KIND_WEBHOOK,
        "target": row.target,
        "secret": row.secret,
        "timeout_seconds": int(row.timeout_seconds or 3),
        "enabled": bool(row.enabled),
    }


async def load_hooks(
    session: AsyncSession, account_id: str, *, event: str | None = None
) -> list[dict[str, Any]]:
    """读账号 enabled hooks 快照(created_at, id 升序);图节点/chat 只消费快照。"""
    rows = await HookRepository(session).list_for_account(
        account_id, event=event, enabled_only=True
    )
    return [_snapshot(row) for row in rows]


async def run_hooks(
    hooks: Sequence[Mapping[str, Any]],
    event: str,
    payload: Mapping[str, Any],
) -> HookOutcome:
    """对一份已有快照求值(chat 等已持快照的服务层挂点用)。

    图内走 ``graph.ports.HookPort``(同一 runner),这里给不方便注入端口的
    服务层留的直调入口;未启用配置时内部直接返回 allow。
    """
    return await _DEFAULT_RUNNER.run(hooks, event, payload)


async def run_for_account(
    session: AsyncSession,
    account_id: str,
    event: str,
    payload: Mapping[str, Any],
) -> HookOutcome:
    """服务层挂点(chat/确认/压缩)的一次性调用:读快照 → 执行。"""
    hooks = await load_hooks(session, account_id)
    return await _DEFAULT_RUNNER.run(hooks, event, payload)


async def notify_after_commit(
    session: AsyncSession,
    account_id: str,
    event: str,
    payload: Mapping[str, Any],
) -> None:
    """会话类事件(§6.3):提交后 best-effort,失败只 warning、不回滚业务。

    有 post_commit 运行时(HTTP 请求路径)就注册到提交后执行;服务层直调
    (单测/脚本)没有运行时,直接 await——两条路径都不阻塞业务返回语义。
    """
    hooks = await load_hooks(session, account_id)
    if not hooks or not _CONFIG.enabled:
        return

    async def _send() -> None:
        await _DEFAULT_RUNNER.run(hooks, event, payload)

    if post_commit.runtime_of(session) is None:
        await _send()
        return
    post_commit.register_after_commit(session, _send)


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
    resolver = _CONFIG.resolver
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
