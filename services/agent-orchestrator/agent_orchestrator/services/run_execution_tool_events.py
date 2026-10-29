"""run.tool.called.v1 事件 payload 的裁剪与装配。

从 services/run_execution_approvals.py 拆出(门面仍由 approvals 全量重导出):
只做纯数据整理,不碰 session/事件,便于单测与复用。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

#: 单条 run.tool.called.v1 的 payload 序列化上限(设计 §15.3:≤16KB)。
MAX_TOOL_EVENT_BYTES = 16 * 1024
#: payload 里 arguments 的展示上限(超长参数只留前段,避免事件体积爆炸)。
MAX_EVENT_ARGUMENTS_CHARS = 2000
#: payload 里 paths 的条数/单条长度上限(§11.1:事件只留前 20 条)。
MAX_EVENT_PATHS = 20
MAX_EVENT_PATH_CHARS = 512
#: sha256 只留原值长度(64 位十六进制),更长的一律截断。
MAX_EVENT_SHA_CHARS = 64


def _event_text(value: Any, limit: int) -> str:
    """任意值 -> 限长字符串(None/空值统一成空串)。"""

    return str(value or "")[:limit]


def _event_int(value: Any) -> int:
    """只接受真整数(bool 不算),其余归零(审计字段容错)。"""

    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _event_paths(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        _event_text(item, MAX_EVENT_PATH_CHARS)
        for item in value[:MAX_EVENT_PATHS]
        if item
    ]


def _event_sha(value: Any) -> str | None:
    text = _event_text(value, MAX_EVENT_SHA_CHARS)
    return text or None


def _payload_bytes(payload: dict[str, Any]) -> int:
    """payload 实际序列化后的字节数(ensure_ascii=False,与落库口径一致)。"""

    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _fit_tool_event(payload: dict[str, Any]) -> dict[str, Any]:
    """保证单条 payload ≤16KB:先截 arguments,再截 paths,最后收 detail。"""

    if _payload_bytes(payload) <= MAX_TOOL_EVENT_BYTES:
        return payload
    for limit in (1000, 500, 200, 0):
        payload["arguments"] = payload["arguments"][:limit]
        if _payload_bytes(payload) <= MAX_TOOL_EVENT_BYTES:
            return payload
    while payload["paths"] and _payload_bytes(payload) > MAX_TOOL_EVENT_BYTES:
        payload["paths"] = payload["paths"][:-1]
    while payload["detail"] and _payload_bytes(payload) > MAX_TOOL_EVENT_BYTES:
        payload["detail"] = payload["detail"][: len(payload["detail"]) // 2]
    return payload


def _tool_event_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """把 tool_calls 记录整理成 run.tool.called.v1 的 payload。

    P0 字段 name/arguments/ok/detail 原样保留(detail 仍截 200 字符),
    P1 扩充审计字段(§11.1/§12.3):tool_call_id、risk、decision、
    error_code、duration_ms、result_chars、truncated、paths、
    sha256_before/after。P1-F 再加 offloaded/offload_path/original_chars:
    工具结果被 offload 成文件时,事件能回答"原文落在哪个文件、多大"。
    事件 type 与 seq 语义不变,新字段全部可选。
    """

    arguments = _event_text(record.get("arguments"), MAX_EVENT_ARGUMENTS_CHARS)
    payload: dict[str, Any] = {
        "name": _event_text(record.get("name"), 200),
        "arguments": arguments,
        "ok": bool(record.get("ok")),
        "detail": _event_text(record.get("detail"), 200),
        "tool_call_id": _event_text(record.get("tool_call_id"), 128),
        "risk": _event_text(record.get("risk"), 16) or "danger",
        "decision": _event_text(record.get("decision"), 16) or "auto",
        "error_code": _event_text(record.get("error_code"), 64) or None,
        "duration_ms": _event_int(record.get("duration_ms")),
        "result_chars": _event_int(record.get("result_chars")),
        "truncated": bool(record.get("truncated")),
        "paths": _event_paths(record.get("paths")),
        "sha256_before": _event_sha(record.get("sha256_before")),
        "sha256_after": _event_sha(record.get("sha256_after")),
        # P1-F:大结果 offload 后事件里留引用(模型只看到信封,审计要看全文去哪了)
        "offloaded": bool(record.get("offloaded")),
        "offload_path": _event_text(record.get("offload_path"), MAX_EVENT_PATH_CHARS)
        or None,
        "original_chars": _event_int(record.get("original_chars")),
    }
    return _fit_tool_event(payload)
