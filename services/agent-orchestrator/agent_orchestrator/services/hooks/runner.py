"""Webhook 执行器:按事件逐个投递、验签、解析决策。"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from common.logging import get_logger

from agent_orchestrator.graph.ports import HookDecision, HookOutcome
from agent_orchestrator.services import net_guard

from .config import current_config
from .constants import (
    EVENT_HEADER,
    ID_HEADER,
    LOGGER_NAME,
    MAX_TIMEOUT_SECONDS,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    HookConfig,
)
from .protocol import (
    _join_notes,
    _matches,
    _read_limited,
    _timeout_for,
    parse_response,
    sign,
)

logger = get_logger(LOGGER_NAME)
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
        config = current_config()
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
