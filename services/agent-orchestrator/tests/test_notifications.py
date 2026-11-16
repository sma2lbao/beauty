"""通知持久化:投影纯函数、sweeper 幂等、清单端点、已读与 SSE 回放。

口径:通知是 run_events 的投影——sweeper 把"需要人关注"的状态幂等写进
notifications 表,列表端点给未读清单,SSE 只是这个表的增量尾巴。
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from database.session import session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_orchestrator.models.notification import Notification
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import NotificationRepository
from agent_orchestrator.services import notifications as notifications_service
from agent_orchestrator.services.notifications import (
    _cursor,
    _parse_cursor,
    notification_for,
    notification_stream,
)
from tests.fixtures.auth import ACCOUNT, OTHER

#: conftest 里 ACCOUNT = _IDP.bearer("t-1")
ACCOUNT_ID = "t-1"
OTHER_ID = "t-2"


def _run(
    run_id: str = "r1",
    status: str = "completed",
    conversation_id: str | None = None,
    *,
    goal: str = "把日程整理成周报",
):
    return SimpleNamespace(
        id=run_id,
        status=status,
        title="周报",
        goal=goal,
        conversation_id=conversation_id,
    )


def _settings(**overrides) -> SimpleNamespace:
    """sweep_once/stream 只读这几个字段,用轻量替身免造整套 Settings。"""
    values = {
        "notifications_lookback_seconds": 120,
        "notifications_batch_limit": 100,
        "notifications_retention_days": 30,
        "notifications_poll_interval_seconds": 0.001,
        "sse_keepalive_polls": 1,
        "notifications_sweep_interval_seconds": 0.001,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _seed_run(factory, *, status: str = "completed", **kwargs) -> str:
    async with session_scope(factory) as session:
        run = Run(
            account_id=kwargs.pop("account_id", ACCOUNT_ID),
            title="周报",
            goal="把日程整理成周报",
            status=status,
            **kwargs,
        )
        session.add(run)
        await session.flush()
        return run.id


async def _seed_notifications(
    factory, count: int, *, account_id: str = ACCOUNT_ID
) -> list[Notification]:
    """按时间递增写 count 条通知,返回落库后的行(正序)。"""
    start = datetime.now(UTC) - timedelta(seconds=10)
    async with session_scope(factory) as session:
        repository = NotificationRepository(session)
        for index in range(count):
            await repository.insert_if_absent(
                account_id=account_id,
                kind="run.status",
                dedupe_key=f"r{index}:completed",
                title=f"通知{index}",
                body="整理完了",
                tone="success",
                run_id=f"r{index}",
                created_at=start + timedelta(seconds=index),
            )
        return list(
            await session.scalars(
                select(Notification)
                .where(Notification.account_id == account_id)
                .order_by(Notification.created_at)
            )
        )


def test_notification_for_七种需要提醒的状态都有落表字段() -> None:
    cases = {
        "awaiting_approval": ("任务待审批", "warning"),
        "awaiting_confirmation": ("任务待确认危险操作", "warning"),
        "awaiting_input": ("任务等待你的回答", "warning"),
        "completed": ("任务已完成", "success"),
        "failed": ("任务失败", "danger"),
        "blocked": ("任务被阻塞", "danger"),
        "cancelled": ("任务已取消", "info"),
    }
    for status, (title, tone) in cases.items():
        payload = notification_for(_run("r1", status, "c1"))
        assert payload is not None
        assert payload["kind"] == "run.status"
        assert payload["dedupe_key"] == f"r1:{status}"
        assert payload["title"] == title
        assert payload["tone"] == tone
        assert payload["to_path"] == "/?conversation=c1"
        assert payload["run_id"] == "r1"
        assert payload["conversation_id"] == "c1"
        assert payload["payload"]["status"] == status
        assert payload["body"] == "把日程整理成周报"


def test_notification_for_中间态不产通知且直建run跳任务页() -> None:
    assert notification_for(_run("r1", "running")) is None
    assert notification_for(_run("r1", "pending")) is None
    direct = notification_for(_run("r1", "completed"))
    assert direct is not None
    assert direct["to_path"] == "/runs"
    # 摘要截断到 300 字,避免把整篇目标塞进通知详情
    long = notification_for(_run("r1", "failed", goal="目" * 400))
    assert long is not None
    assert len(long["body"]) == 300


def test_cursor_编码解析往返与非法值() -> None:
    moment = datetime(2026, 11, 16, 12, 0, 0, 123456, tzinfo=UTC)
    encoded = _cursor(moment, "abc")
    assert encoded == "2026-11-16T12:00:00.123456+00:00|abc"
    assert _parse_cursor(encoded) == (moment, "abc")
    assert _parse_cursor(None) is None
    assert _parse_cursor("") is None
    assert _parse_cursor("no-separator") is None
    assert _parse_cursor("not-a-date|abc") is None


def test_sweep_once_幂等落库并按保留期清理已读(client: TestClient) -> None:
    factory = client.app.state.session_factory
    settings = _settings()

    async def scenario() -> None:
        run_id = await _seed_run(factory, status="completed", conversation_id="c1")
        first = await notifications_service.sweep_once(factory, settings)
        assert first["created"] == 1
        assert first["purged"] == 0

        # 再扫一轮:同一 run 同一状态不重复写
        second = await notifications_service.sweep_once(factory, settings)
        assert second["created"] == 0

        async with session_scope(factory) as session:
            repository = NotificationRepository(session)
            assert await repository.unread_count(ACCOUNT_ID) == 1
            rows = await repository.list_recent(ACCOUNT_ID)
            assert [row.run_id for row in rows] == [run_id]
            assert rows[0].read_at is None

        # 已读且早于保留期的行会在下一轮被清掉;未读行不动
        old = datetime.now(UTC) - timedelta(days=40)
        async with session_scope(factory) as session:
            repository = NotificationRepository(session)
            await repository.insert_if_absent(
                account_id=ACCOUNT_ID,
                kind="run.status",
                dedupe_key="old:completed",
                title="旧通知",
                body="很久以前",
                tone="success",
                created_at=old,
            )
            stale = await repository.list_recent(ACCOUNT_ID, limit=10)
            stale_id = next(
                row.id for row in stale if row.dedupe_key == "old:completed"
            )
            await repository.mark_read(ACCOUNT_ID, stale_id)

        third = await notifications_service.sweep_once(factory, settings)
        assert third["purged"] == 1
        async with session_scope(factory) as session:
            repository = NotificationRepository(session)
            remaining = await repository.list_recent(ACCOUNT_ID)
            assert [row.dedupe_key for row in remaining] == [f"{run_id}:completed"]

    asyncio.run(scenario())


def test_list_端点_分页游标与未读数(client: TestClient) -> None:
    factory = client.app.state.session_factory
    rows = asyncio.run(_seed_notifications(factory, 3))

    resp = client.get("/api/v1/notifications?limit=2", headers=ACCOUNT)
    assert resp.status_code == 200
    body = resp.json()
    assert body["unread_count"] == 3
    assert len(body["items"]) == 2
    # 最新在前
    assert body["items"][0]["id"] == rows[2].id
    assert body["next_cursor"] is not None

    page2 = client.get(
        "/api/v1/notifications",
        params={"limit": 2, "before": body["next_cursor"]},
        headers=ACCOUNT,
    )
    assert page2.status_code == 200
    assert [item["id"] for item in page2.json()["items"]] == [rows[0].id]
    assert page2.json()["next_cursor"] is None

    # 别的账号看不到这些通知
    other = client.get("/api/v1/notifications", headers=OTHER)
    assert other.status_code == 200
    assert other.json() == {"items": [], "unread_count": 0, "next_cursor": None}

    assert client.get("/api/v1/notifications").status_code == 401


def test_单条已读_全部已读与跨账号404(client: TestClient) -> None:
    factory = client.app.state.session_factory
    rows = asyncio.run(_seed_notifications(factory, 2))

    resp = client.post(f"/api/v1/notifications/{rows[0].id}/read", headers=ACCOUNT)
    assert resp.status_code == 200
    marked = resp.json()
    assert marked["read_at"] is not None

    # 再点一次不刷新时间(幂等)
    again = client.post(f"/api/v1/notifications/{rows[0].id}/read", headers=ACCOUNT)
    assert again.json()["read_at"] == marked["read_at"]

    listing = client.get("/api/v1/notifications", headers=ACCOUNT).json()
    assert listing["unread_count"] == 1

    # 跨账号探测一律 404,不泄漏存在性
    cross = client.post(f"/api/v1/notifications/{rows[0].id}/read", headers=OTHER)
    assert cross.status_code == 404
    assert cross.json()["code"] == "notification.not_found"

    read_all = client.post("/api/v1/notifications/read-all", headers=ACCOUNT)
    assert read_all.status_code == 200
    assert read_all.json() == {"updated": 1}
    assert (
        client.get("/api/v1/notifications", headers=ACCOUNT).json()["unread_count"] == 0
    )


def test_stream_首帧就绪且按游标回放(client: TestClient) -> None:
    factory = client.app.state.session_factory
    rows = asyncio.run(_seed_notifications(factory, 2))
    settings = _settings()

    async def scenario() -> list[str]:
        gen = notification_stream(
            factory,
            settings,
            ACCOUNT_ID,
            _cursor(rows[0].created_at, rows[0].id),
        )
        try:
            frames = [await gen.__anext__()]
            frames.append(await gen.__anext__())
        finally:
            await gen.aclose()
        return frames

    frames = asyncio.run(scenario())
    assert frames[0] == ": ready" + chr(10) * 2
    assert f"id: {_cursor(rows[1].created_at, rows[1].id)}" in frames[1]
    assert "notification.v1" in frames[1]
    payload = json.loads(
        next(line[6:] for line in frames[1].splitlines() if line.startswith("data: "))
    )
    assert payload["id"] == rows[1].id
    assert payload["run_id"] == "r1"
    assert payload["read_at"] is None


def test_stream_无新行时打keep_alive(client: TestClient) -> None:
    factory = client.app.state.session_factory
    asyncio.run(_seed_notifications(factory, 1))

    async def scenario() -> str:
        # created_at 全在过去 → 不带 Last-Event-ID 时从"此刻"开始,不回放历史
        gen = notification_stream(factory, _settings(), ACCOUNT_ID)
        try:
            assert await gen.__anext__() == ": ready" + chr(10) * 2
            return await gen.__anext__()
        finally:
            await gen.aclose()

    assert asyncio.run(scenario()) == ": keep-alive" + chr(10) * 2


def test_stream_未带账号令牌被拒(client: TestClient) -> None:
    assert client.get("/api/v1/notifications/stream").status_code == 401


def test_sweeper_间隔为零或没有数据库时直接退出(client: TestClient) -> None:
    container = SimpleNamespace(
        settings=_settings(notifications_sweep_interval_seconds=0.0),
        session_factory=client.app.state.session_factory,
    )
    asyncio.run(
        asyncio.wait_for(notifications_service.sweeper_loop(container), timeout=1)
    )
    blank = SimpleNamespace(settings=_settings(), session_factory=None)
    asyncio.run(asyncio.wait_for(notifications_service.sweeper_loop(blank), timeout=1))


def test_sweeper_异常不退出且可被取消(client: TestClient) -> None:
    """写入失败只记 warning,下一轮继续;stop 时取消任务不会泄漏。"""

    async def scenario() -> None:
        broken = SimpleNamespace(settings=_settings(), session_factory=object())
        task = notifications_service.start_sweeper(broken)
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
