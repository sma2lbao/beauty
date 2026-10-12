"""日历 ICS:解析(折行/时区参数/排序)、缺文件语义、工具层装配。"""

import json
from pathlib import Path

import pytest
from common.errors import StorageError

from agent_orchestrator.services.calendar import CalendarFeed, parse_ics
from agent_orchestrator.services.tools import build_tools

ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:2@example.com
DTSTART;TZID=Asia/Shanghai:20261015T140000
SUMMARY:产品评审
LOCATION:会议室 A
END:VEVENT
BEGIN:VEVENT
UID:1@example.com
DTSTART:20261013T093000Z
DTEND:20261013T100000Z
SUMMARY:长标题折行
 第二行
END:VEVENT
BEGIN:VEVENT
UID:3@example.com
SUMMARY:没有开始时间的事件
END:VEVENT
END:VCALENDAR
"""


def test_parse_sorts_by_start_and_unfolds() -> None:
    events = parse_ics(ICS)
    assert [event.summary for event in events] == ["长标题折行第二行", "产品评审"]
    assert events[0].starts_at == "20261013T093000Z"
    assert events[0].ends_at == "20261013T100000Z"
    assert events[1].location == "会议室 A"
    assert events[1].ends_at is None
    # 无开始时间的事件被跳过(无法排序/展示)
    assert all(event.uid != "3@example.com" for event in events)


def test_parse_respects_limit() -> None:
    assert len(parse_ics(ICS, limit=1)) == 1


def test_unparseable_start_sorted_last() -> None:
    text = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nSUMMARY:坏时间\nDTSTART:not-a-date\nEND:VEVENT\n"
        "BEGIN:VEVENT\nSUMMARY:好时间\nDTSTART:20261013T090000\nEND:VEVENT\n"
        "END:VCALENDAR\n"
    )
    assert [event.summary for event in parse_ics(text)] == ["好时间", "坏时间"]


async def test_feed_missing_file(tmp_path: Path) -> None:
    feed = CalendarFeed(tmp_path / "nope.ics")
    with pytest.raises(StorageError) as exc_info:
        await feed.events()
    assert exc_info.value.code == "calendar.feed_missing"


async def test_feed_and_tools(tmp_path: Path) -> None:
    path = tmp_path / "cal.ics"
    path.write_text(ICS, encoding="utf-8")
    feed = CalendarFeed(path)

    assert len(await feed.events(limit=1)) == 1

    registry = build_tools("t-1", calendar=feed)
    assert registry.names == ["list_calendar_events"]
    payload = json.loads(await registry.execute("list_calendar_events", "{}"))
    assert [event["summary"] for event in payload["events"]] == [
        "长标题折行第二行",
        "产品评审",
    ]

    # limit 越界被夹取,不会报错
    clamped = json.loads(
        await registry.execute("list_calendar_events", '{"limit": 999}')
    )
    assert len(clamped["events"]) == 2
