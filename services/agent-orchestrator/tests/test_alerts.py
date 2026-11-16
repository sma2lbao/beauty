"""运维告警单测:规则边界、sweep 去重/恢复/保留期、读取面口径。

全部用临时 sqlite 文件库 + 直接调 `sweep_once`(显式传 `now`),不跑后台循环、
不依赖真实时钟、零网络。时间统一用 UTC aware datetime 写入,与生产口径一致。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from database.base import Base
from database.session import create_session_factory, session_scope
from events.outbox import DISPATCHED, FAILED, PENDING, OutboxEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app
from agent_orchestrator.models.alert import FIRING, RESOLVED, Alert
from agent_orchestrator.models.runs import Job, Run
from agent_orchestrator.services import alerts
from tests.conftest import _settings

ACCOUNT = "t-1"


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """临时 sqlite 文件库 + 建表;用例结束 dispose,避免句柄残留。"""
    settings = _settings(tmp_path, "alerts.db")
    session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield session_factory
    finally:
        await session_factory.engine.dispose()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """告警默认阈值(与生产一致);sweeper 循环在用例里手动调,故间隔无关。"""
    return _settings(tmp_path, "alerts.db", alerts_enabled=True)


async def _add_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str,
    updated_at: datetime | None = None,
) -> str:
    async with session_scope(factory) as session:
        run = Run(account_id=ACCOUNT, title="任务", goal="目标", status=status)
        if updated_at is not None:
            run.updated_at = updated_at
        session.add(run)
        await session.flush()
        return run.id


async def _add_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str,
    updated_at: datetime | None = None,
) -> None:
    async with session_scope(factory) as session:
        job = Job(
            account_id=ACCOUNT,
            kind="plan",
            run_id="r-1",
            status=status,
            run_at=datetime.now(UTC),
        )
        if updated_at is not None:
            job.updated_at = updated_at
        session.add(job)


async def _add_outbox(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str = PENDING,
    available_at: datetime | None = None,
) -> None:
    async with session_scope(factory) as session:
        event = OutboxEvent(
            account_id=ACCOUNT,
            event_type="run.completed.v1",
            occurred_at=datetime.now(UTC).isoformat(),
            status=status,
            available_at=available_at or datetime.now(UTC),
        )
        session.add(event)


async def _add_alert(
    factory: async_sessionmaker[AsyncSession],
    *,
    code: str,
    status: str,
    created_at: datetime | None = None,
    bucket: int = 0,
) -> None:
    async with session_scope(factory) as session:
        alert = Alert(
            code=code,
            severity="warning",
            status=status,
            title="历史事件",
            summary="",
            bucket=bucket,
        )
        if created_at is not None:
            alert.created_at = created_at
        session.add(alert)


async def _codes(
    factory: async_sessionmaker[AsyncSession], settings: Settings, now: datetime
) -> set[str]:
    async with session_scope(factory) as session:
        return {signal.code for signal in await alerts.evaluate(session, settings, now)}


# ---------------------------------------------------------------- 规则边界


async def test_empty_database_reports_nothing(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """空库不该有任何告警(冷启动不误报)。"""
    now = datetime.now(UTC)
    assert await _codes(factory, settings, now) == set()


async def test_outbox_failed_is_critical(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """failed 是"重试耗尽、不会自动再试",阈值 >0 且定 critical。"""
    await _add_outbox(factory, status=FAILED)
    async with session_scope(factory) as session:
        signals = await alerts.evaluate(session, settings, datetime.now(UTC))
    by_code = {signal.code: signal for signal in signals}
    assert set(by_code) == {"outbox_failed"}
    signal = by_code["outbox_failed"]
    assert signal.severity == "critical"
    assert signal.value == 1.0
    assert signal.threshold == 0.0


async def test_outbox_backlog_only_when_older_than_threshold(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """刚入队(pending 且未到重试时间)是正常状态,只有超过 backlog 秒才算积压。"""
    now = datetime.now(UTC)
    await _add_outbox(factory, available_at=now - timedelta(seconds=10))
    assert await _codes(factory, settings, now) == set()

    await _add_outbox(
        factory,
        available_at=now
        - timedelta(seconds=settings.alerts_outbox_backlog_seconds + 60),
    )
    assert await _codes(factory, settings, now) == {"outbox_backlog"}


async def test_outbox_dispatched_does_not_trigger(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """已投递的事件无论多旧都不是积压。"""
    now = datetime.now(UTC)
    await _add_outbox(
        factory,
        status=DISPATCHED,
        available_at=now - timedelta(days=1),
    )
    assert await _codes(factory, settings, now) == set()


async def test_run_stuck_requires_running_and_stale(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """只有"还在 running 且长时间没更新"才算卡死:终态再旧也不报。"""
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=settings.alerts_run_stuck_seconds + 60)
    await _add_run(factory, status="running", updated_at=now)
    await _add_run(factory, status="completed", updated_at=stale)
    assert await _codes(factory, settings, now) == set()

    await _add_run(factory, status="running", updated_at=stale)
    assert await _codes(factory, settings, now) == {"run_stuck"}


async def test_run_failure_rate_needs_min_samples(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """样本不足时不做失败率判定:两个新部署里失败两个≠线上故障。"""
    now = datetime.now(UTC)
    for _ in range(2):
        await _add_run(
            factory,
            status="failed",
            updated_at=now - timedelta(seconds=30),
        )
    assert await _codes(factory, settings, now) == set()

    # 补到最小样本量(5),失败占比 3/5=0.6 ≥ 0.5
    for _ in range(2):
        await _add_run(
            factory,
            status="completed",
            updated_at=now - timedelta(seconds=30),
        )
    await _add_run(factory, status="failed", updated_at=now - timedelta(seconds=30))
    assert await _codes(factory, settings, now) == {"run_failure_rate"}


async def test_run_failure_rate_ignores_out_of_window(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """窗口外的历史失败不参与本轮判定。"""
    now = datetime.now(UTC)
    old = now - timedelta(seconds=settings.alerts_failure_window_seconds + 600)
    for _ in range(5):
        await _add_run(factory, status="failed", updated_at=old)
    assert await _codes(factory, settings, now) == set()


async def test_job_failures_respects_threshold(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """单个 job 失败常见(上游抖动),到阈值才报。"""
    now = datetime.now(UTC)
    recent = now - timedelta(seconds=60)
    for _ in range(settings.alerts_job_failure_threshold - 1):
        await _add_job(factory, status="failed", updated_at=recent)
    assert await _codes(factory, settings, now) == set()

    await _add_job(factory, status="failed", updated_at=recent)
    assert await _codes(factory, settings, now) == {"job_failures"}


# ------------------------------------------------------- sweep:去重与恢复


async def test_sweep_fires_once_then_resolves(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """状态切换才写事件:firing 只写一次;条件消失写 resolved 并计数。"""
    now = datetime.now(UTC)
    await _add_outbox(factory, status=FAILED)

    first = await alerts.sweep_once(factory, settings, now=now)
    assert first == {"fired": 1, "resolved": 0, "purged": 0}
    # 同桶内再来一轮:状态已是 firing,不重复写
    second = await alerts.sweep_once(factory, settings, now=now)
    assert second == {"fired": 0, "resolved": 0, "purged": 0}
    # 下一桶仍然 firing:依旧不重复写(去重靠状态,不靠桶)
    third = await alerts.sweep_once(
        factory, settings, now=now + timedelta(seconds=settings.alerts_cooldown_seconds)
    )
    assert third == {"fired": 0, "resolved": 0, "purged": 0}

    # 条件消失(人工重放成功):下一轮写 resolved
    async with session_scope(factory) as session:
        event = (await session.execute(select(OutboxEvent))).scalars().one()
        event.status = DISPATCHED
    resolved = await alerts.sweep_once(
        factory,
        settings,
        now=now + 2 * timedelta(seconds=settings.alerts_cooldown_seconds),
    )
    assert resolved == {"fired": 0, "resolved": 1, "purged": 0}

    async with session_scope(factory) as session:
        view = await alerts.snapshot(session)
    assert view["firing"] == []
    assert [item["code"] for item in view["resolved"]] == ["outbox_failed"]
    assert view["resolved"][0]["resolved_at"]


async def test_sweep_survives_dedupe_race(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """多副本并发:同桶同状态已被别的副本写入 → 放弃本轮写入,不抛异常。

    模拟方式:同桶里先落一条 firing,再让 current_states 返回空(相当于本副本
    还没看到那条行),于是写入撞唯一约束——这正是真实竞态的形状。
    """
    now = datetime.now(UTC)
    await _add_outbox(factory, status=FAILED)
    await _add_alert(
        factory,
        code="outbox_failed",
        status=FIRING,
        bucket=alerts._bucket(now, settings),
    )

    async def _empty(session: AsyncSession) -> dict[str, str]:
        return {}

    monkeypatch.setattr(alerts, "current_states", _empty)
    result = await alerts.sweep_once(factory, settings, now=now)
    assert result == {"fired": 0, "resolved": 0, "purged": 0}

    async with session_scope(factory) as session:
        rows = (await session.execute(select(Alert))).scalars().all()
    assert len(rows) == 1


async def test_retention_purges_only_old_resolved(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """保留期只清历史 resolved;firing 是待办,不因为旧就删。"""
    now = datetime.now(UTC)
    old = now - timedelta(days=settings.alerts_retention_days + 1)
    # run_failure_rate 没有历史事件又无信号 → 不写 resolved,只有两条旧行被清;
    # 同 code+status 要靠不同 bucket 才能共存(唯一约束口径与生产一致)。
    await _add_alert(
        factory, code="run_failure_rate", status=RESOLVED, created_at=old, bucket=1
    )
    await _add_alert(
        factory,
        code="run_failure_rate",
        status=RESOLVED,
        created_at=old - timedelta(seconds=1),
        bucket=2,
    )
    # run_stuck 当前仍然成立(卡死的 run),旧 firing 行必须留下
    await _add_run(
        factory,
        status="running",
        updated_at=now - timedelta(seconds=settings.alerts_run_stuck_seconds + 60),
    )
    await _add_alert(factory, code="run_stuck", status=FIRING, created_at=old)

    result = await alerts.sweep_once(factory, settings, now=now)
    assert result == {"fired": 0, "resolved": 0, "purged": 2}

    async with session_scope(factory) as session:
        codes = {row.code for row in (await session.execute(select(Alert))).scalars()}
    assert codes == {"run_stuck"}


async def test_snapshot_shows_latest_state_per_code(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """firing 只取每个 code 的最新状态:恢复后不再出现在 firing 里。"""
    now = datetime.now(UTC)
    await _add_alert(
        factory, code="run_stuck", status=FIRING, created_at=now - timedelta(minutes=5)
    )
    await _add_alert(factory, code="run_stuck", status=RESOLVED, created_at=now)

    async with session_scope(factory) as session:
        view = await alerts.snapshot(session)
    assert view["firing"] == []
    assert len(view["resolved"]) == 1


# --------------------------------------------------------- sweeper 循环


async def test_sweeper_loop_stops_when_interval_disabled(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    """间隔 <= 0 直接返回:测试/单进程场景不该起空转任务。"""
    container = SimpleNamespace(
        settings=SimpleNamespace(alerts_sweep_interval_seconds=0.0),
        session_factory=factory,
    )
    await asyncio.wait_for(alerts.sweeper_loop(container), timeout=1.0)


async def test_sweeper_loop_keeps_running_after_failure(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """单轮评估失败只记日志:循环必须活着,否则一次库抖动就永久失去告警。"""
    calls: list[int] = []

    async def _boom(*args: Any, **kwargs: Any) -> dict[str, int]:
        calls.append(1)
        raise RuntimeError("db unavailable")

    settings = SimpleNamespace(alerts_sweep_interval_seconds=0.01)
    monkeypatch.setattr(alerts, "sweep_once", _boom)
    container = SimpleNamespace(settings=settings, session_factory=factory)
    task = asyncio.create_task(alerts.sweeper_loop(container))
    try:
        for _ in range(100):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert len(calls) >= 2


# --------------------------------------------------------------- 读取面


def test_alerts_endpoint_not_registered_when_disabled(tmp_path: Path) -> None:
    """关掉告警时端点不存在(不是"存在但空"):不暴露运维面。"""
    from fastapi.testclient import TestClient

    app = create_app(_settings(tmp_path, "alerts-off.db", alerts_enabled=False))
    with TestClient(app) as client:
        assert client.get("/internal/alerts").status_code == 404


def test_alerts_endpoint_requires_metrics_token(tmp_path: Path) -> None:
    """与 /internal/metrics 同口径:配了令牌就必须带,不符按 404。"""
    from fastapi.testclient import TestClient

    app = create_app(
        _settings(
            tmp_path,
            "alerts-on.db",
            alerts_enabled=True,
            alerts_sweep_interval_seconds=0.0,
            metrics_token="s3cret",
        )
    )
    with TestClient(app) as client:
        assert client.get("/internal/alerts").status_code == 404
        assert (
            client.get(
                "/internal/alerts", headers={"X-Metrics-Token": "wrong"}
            ).status_code
            == 404
        )
        response = client.get("/internal/alerts", headers={"X-Metrics-Token": "s3cret"})
        assert response.status_code == 200
        assert response.json() == {"firing": [], "resolved": []}


def test_alerts_endpoint_returns_firing_rows(tmp_path: Path) -> None:
    """端点返回的是当前视图:库里有一行 firing 就能读到(先落库再起应用)。"""
    from fastapi.testclient import TestClient

    settings = _settings(
        tmp_path,
        "alerts-view.db",
        alerts_enabled=True,
        alerts_sweep_interval_seconds=0.0,
    )
    factory = create_session_factory(settings.database_url)

    async def _seed() -> None:
        async with factory.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await _add_alert(factory, code="outbox_failed", status=FIRING)
        await factory.engine.dispose()

    asyncio.run(_seed())
    with TestClient(create_app(settings)) as client:
        response = client.get("/internal/alerts")
    assert response.status_code == 200
    payload = response.json()
    assert [item["code"] for item in payload["firing"]] == ["outbox_failed"]
    assert payload["firing"][0]["severity"] == "warning"
    assert payload["firing"][0]["since"]
