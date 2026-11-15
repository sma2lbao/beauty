"""作业登记行的只读快照、状态读取结果与纯函数辅助。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tool_service.models.background_job import BackgroundJob
from tool_service.services.execution import job_scripts


@dataclass(frozen=True, slots=True)
class _JobSnapshot:
    """登记行在事务内的只读快照(ORM 属性统一在 session 内读出来)。"""

    job_id: str
    sandbox_id: str
    status: str
    exit_code: int | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class _JobReading:
    """一次状态脚本的解析结果。"""

    state: str
    exit_code: int | None
    stdout: str
    stderr: str


# ---- 纯函数辅助(便于单测,不碰 IO)------------------------------------


def _snapshot(row: BackgroundJob) -> _JobSnapshot:
    return _JobSnapshot(
        job_id=row.id,
        sandbox_id=row.sandbox_id,
        status=row.status,
        exit_code=row.exit_code,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _reading(output: str) -> _JobReading:
    state, exit_code = job_scripts.parse_status(output)
    stdout_text, stderr_text = job_scripts.split_logs(output)
    return _JobReading(
        state=state,
        exit_code=exit_code,
        stdout=stdout_text,
        stderr=stderr_text,
    )


def _has_pid(output: str | None) -> bool:
    """启动脚本会回显 PID:<pid>;有它才算进程真的起来了。"""
    text = output or ""
    for line in text.splitlines():
        if line.startswith(job_scripts.PID_PREFIX):
            pid = line[len(job_scripts.PID_PREFIX) :].strip()
            return pid.isdigit()
    return False


def _optional_name(arguments: dict[str, Any]) -> str | None:
    raw = arguments.get("name")
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _as_utc(moment: datetime) -> datetime:
    """SQLite 读回来的 datetime 可能是 naive,统一按 UTC 解释。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso_utc(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return _as_utc(moment).isoformat(timespec="microseconds").replace("+00:00", "Z")
