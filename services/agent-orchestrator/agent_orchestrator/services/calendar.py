"""日历来源(§8 技能来源):解析本地 ICS 日历,只读。

诚实的能力边界(阶段 B 补齐):

- 只读 `VEVENT` 的常见字段(SUMMARY/DTSTART/DTEND/LOCATION/UID);
- **不做 RRULE 重复规则展开**,重复事件只出现一次;
- 日期按 ICS 原文解析:`...Z` 视为 UTC,无时区的时间按"无时区"处理
  (因此排序对跨时区日历仅供参考)。
"""

from __future__ import annotations

import re
from asyncio import to_thread
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from common.errors import StorageError

_FOLD = re.compile(r"\r?\n[ \t]")


@dataclass(frozen=True)
class CalendarEvent:
    """一条日历事件。"""

    summary: str
    starts_at: str
    ends_at: str | None
    location: str | None
    uid: str | None


def parse_ics(text: str, *, limit: int = 50) -> list[CalendarEvent]:
    """解析 ICS 文本:展开折行 → 切分 VEVENT → 取字段 → 按开始时间排序。"""
    unfolded = _FOLD.sub("", text.replace("\r\n", "\n"))
    events: list[CalendarEvent] = []
    current: dict[str, str] | None = None
    for raw_line in unfolded.split("\n"):
        line = raw_line.strip()
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current is not None:
                event = _build_event(current)
                if event is not None:
                    events.append(event)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name, _, value = line.partition(":")
        key = name.split(";", 1)[0].upper()
        if key in {"SUMMARY", "DTSTART", "DTEND", "LOCATION", "UID"}:
            current.setdefault(key, value.strip())
    events.sort(key=_sort_key)
    return events[:limit]


def _build_event(fields: dict[str, str]) -> CalendarEvent | None:
    starts_at = fields.get("DTSTART")
    if not starts_at:
        return None  # 无开始时间的事件无法排序/展示,跳过
    return CalendarEvent(
        summary=fields.get("SUMMARY") or "(无标题)",
        starts_at=starts_at,
        ends_at=fields.get("DTEND"),
        location=fields.get("LOCATION"),
        uid=fields.get("UID"),
    )


def _parse_datetime(raw: str) -> datetime | None:
    """解析 ICS 日期/时间(支持 YYYYMMDD、YYYYMMDDTHHMMSS[Z])。"""
    value = raw.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _sort_key(event: CalendarEvent) -> tuple[int, str, str]:
    parsed = _parse_datetime(event.starts_at)
    # 能解析的在前;解析不了的排最后,并按原文兜底保证顺序确定
    return (
        1 if parsed is None else 0,
        parsed.isoformat() if parsed else "",
        event.summary,
    )


class CalendarFeed:
    """本地 ICS 文件只读访问。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    @property
    def path(self) -> Path:
        return self._path

    async def events(self, *, limit: int = 20) -> list[CalendarEvent]:
        if not self._path.exists():
            raise StorageError("calendar.feed_missing", f"日历文件不存在:{self._path}")
        try:
            text = await to_thread(self._path.read_text, "utf-8", "replace")
        except OSError as exc:
            raise StorageError("calendar.read_failed", f"读取日历失败:{exc}") from exc
        return parse_ics(text, limit=limit)
