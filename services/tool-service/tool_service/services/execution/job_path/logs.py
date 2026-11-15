"""作业日志回缩与提示文案:字符预算、等待秒数、超时/lost 说明。"""

from __future__ import annotations

from typing import Any

from common.errors import UpstreamError

from tool_service.constants import MAX_TOOL_CHARS
from tool_service.core.config import Settings
from tool_service.models.background_job import JOB_STATUS_RUNNING
from tool_service.services.execution.exec_output import _payload_chars

from .constants import _LOG_FIT_RESERVE, _MIN_LOG_CHARS
from .snapshot import _JobReading


def _tail_chars(settings: Settings, arguments: dict[str, Any]) -> int:
    maximum = max(1000, int(settings.job_output_max_chars or 20000))
    default = min(max(1000, int(settings.job_output_default_chars or 8000)), maximum)
    raw = arguments.get("tail_chars")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return default
    return min(max(raw, 1000), maximum)


def _stderr_chars(tail_chars: int) -> int:
    """stderr 取 stdout 的一半且不超过 4000,保证整个信封仍装得下。"""
    return max(500, min(4000, tail_chars // 2))


def _wait_seconds(arguments: dict[str, Any]) -> int:
    raw = arguments.get("wait_seconds")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return min(max(raw, 0), 15)


def _is_gone(exc: UpstreamError) -> bool:
    """沙箱服务明确回 404(沙箱被回收)才判 lost。"""
    details = getattr(exc, "details", None) or {}
    return details.get("status") == 404


def _timeout_note(runtime_seconds: int, reading: _JobReading | None) -> str:
    if reading is None:
        return f"作业运行超过 {runtime_seconds} 秒,已标记 timeout;终止请求未能送达沙箱"
    return f"作业运行超过 {runtime_seconds} 秒,已终止并标记 timeout"


def _output_notes(tail_chars: int, status: str, note: str) -> list[str]:
    notes = [
        f"stdout 只保留最后 {tail_chars} 字符,stderr 只保留最后"
        f" {_stderr_chars(tail_chars)} 字符;完整输出请 read 日志文件"
    ]
    if note:
        notes.append(note)
    if status == JOB_STATUS_RUNNING:
        notes.append("作业仍在运行,可稍后再用 job_output 查看")
    return notes


def _fit_job_logs(
    payload: dict[str, Any], limit: int = MAX_TOOL_CHARS - _LOG_FIT_RESERVE
) -> tuple[dict[str, Any], bool]:
    """整包回缩到 limit 字符内:保留日志尾部(最新输出),至少各留 200 字符。"""
    adjusted = dict(payload)
    shrunk = False
    while _payload_chars(adjusted) > limit:
        field = max(
            ("stdout", "stderr"),
            key=lambda key: len(str(adjusted.get(key) or "")),
        )
        text = str(adjusted.get(field) or "")
        if len(text) <= _MIN_LOG_CHARS:
            break
        overflow = _payload_chars(adjusted) - limit
        cut = min(len(text) - _MIN_LOG_CHARS, max(overflow + 64, 256))
        if cut <= 0:
            break
        adjusted[field] = text[cut:]
        shrunk = True
    return adjusted, shrunk
