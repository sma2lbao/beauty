"""Webhook 协议编解码:参数截断/清洗、HMAC 签名、响应解析、事件匹配与限长读取。"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from common.logging import get_logger

from agent_orchestrator.graph.ports import (
    HOOK_EVENT_PRE_COMPACT,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_TOOL_EVENTS,
    HookDecision,
    HookOutcome,
)

from .constants import (
    _CONTROL_CHARS,
    ARGUMENT_CHARS,
    LOGGER_NAME,
    MAX_TIMEOUT_SECONDS,
    NOTE_CHARS,
    REASON_CHARS,
    RESPONSE_MAX_BYTES,
    HookConfig,
)

logger = get_logger(LOGGER_NAME)
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
