"""exec 输出处理:按上限裁流、整包回缩、识别沙箱侧超时。"""

from __future__ import annotations

import json
from typing import Any

from common.errors import UpstreamError

from tool_service.constants import MIN_EXEC_STDERR_CHARS, MIN_EXEC_STDOUT_CHARS


def _clip_exec_stream(raw: str | None, limit: int, *, keep: str) -> tuple[str, bool]:
    """按上限裁一路输出(keep=tail 保留末尾,适合错误栈)。"""

    text = raw or ""
    if len(text) <= limit:
        return text, False
    if keep == "tail":
        return text[-limit:], True
    return text[:limit], True


def _payload_chars(result: dict[str, Any]) -> int:
    """结果字典的近似序列化体积(按回包 JSON 口径)。"""

    return len(json.dumps(result, ensure_ascii=False, default=str))


def _fit_exec_result(result: dict[str, Any], limit: int) -> tuple[dict[str, Any], bool]:
    """把整包结果回缩到 limit 字符内(先减 stdout 再减 stderr,到底再对半砍)。"""

    adjusted = dict(result)
    shrunk = False
    while _payload_chars(adjusted) > limit:
        overflow = _payload_chars(adjusted) - limit
        reduced = False
        for field, floor, keep_head in (
            ("stdout", MIN_EXEC_STDOUT_CHARS, True),
            ("stderr", MIN_EXEC_STDERR_CHARS, False),
        ):
            text = str(adjusted.get(field) or "")
            if len(text) <= floor:
                continue
            cut = min(len(text) - floor, max(overflow + 64, 256))
            adjusted[field] = text[: len(text) - cut] if keep_head else text[cut:]
            reduced = True
            shrunk = True
            break
        if reduced:
            continue
        half = False
        for field in ("stdout", "stderr"):
            text = str(adjusted.get(field) or "")
            if text:
                adjusted[field] = text[: len(text) // 2]
                half = True
        if not half:
            break
        shrunk = True
    return adjusted, shrunk


def _is_exec_timeout(exc: UpstreamError) -> bool:
    """识别沙箱侧超时:上游用 sandbox.exec_timeout 区分超时与其它故障。"""

    if str(getattr(exc, "code", "") or "") == "sandbox.exec_timeout":
        return True
    details = getattr(exc, "details", None)
    return "sandbox.exec_timeout" in f"{exc} {details!r}"
