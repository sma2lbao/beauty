"""24h 刷新巡检:候选筛选、失败隔离、后台循环可停。"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from connector.services import patrol
from tests.conftest import _settings, demo_credentials, insert_connection


def _db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _row(db_path: Path, connection_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as raw:
        raw.row_factory = sqlite3.Row
        row = raw.execute(
            "SELECT status, last_error_code, last_refreshed_at "
            "FROM connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
    return dict(row)


def _run_patrol(client: TestClient, settings):
    async def _run() -> int:
        return await patrol.run_patrol_once(
            client.app.state.session_factory,
            settings,
            client.app.state.http_transport,
        )

    return client.portal.call(_run)


def test_patrol_refreshes_only_stale_active_connections(
    client: TestClient, demo, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    settings = _settings(tmp_path)
    stale_at = datetime.now(UTC) - timedelta(days=2)

    stale = insert_connection(db, account="u-stale", last_refreshed_at=stale_at)
    never = insert_connection(db, account="u-never")  # last_refreshed_at 为空
    fresh = insert_connection(
        db,
        account="u-fresh",
        last_refreshed_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    disabled = insert_connection(
        db, account="u-disabled", enabled=False, last_refreshed_at=stale_at
    )
    expired = insert_connection(
        db, account="u-expired", status="expired", last_refreshed_at=stale_at
    )

    assert _run_patrol(client, settings) == 2
    assert demo.refresh_tokens == 2

    for candidate in (stale, never):
        row = _row(db, candidate["id"])
        assert row["status"] == "active"
        assert row["last_error_code"] is None
        assert row["last_refreshed_at"] is not None

    assert _row(db, fresh["id"])["last_refreshed_at"] is not None
    # 只换了两把 token -> 新鲜/停用/已过期连接都没被刷新
    assert demo.refresh_tokens == 2
    assert _row(db, disabled["id"])["status"] == "active"
    assert _row(db, expired["id"])["status"] == "expired"


def test_patrol_failure_is_isolated_and_recorded(
    client: TestClient, demo, tmp_path: Path
) -> None:
    """单条连接刷新失败(需重新授权)只留痕,不影响同轮其它连接。"""
    db = _db(tmp_path)
    settings = _settings(tmp_path)
    stale_at = datetime.now(UTC) - timedelta(days=2)

    broken = insert_connection(
        db,
        account="u-broken",
        last_refreshed_at=stale_at,
        credentials=demo_credentials(refresh_token=None),
    )
    healthy = insert_connection(
        db,
        account="u-healthy",
        last_refreshed_at=stale_at,
        credentials=demo_credentials(access_token="at-ok", refresh_token="rt-ok"),
    )

    assert _run_patrol(client, settings) == 2

    broken_row = _row(db, broken["id"])
    assert broken_row["status"] == "expired"
    assert broken_row["last_error_code"] == "connector.auth_expired"

    healthy_row = _row(db, healthy["id"])
    assert healthy_row["status"] == "active"
    assert healthy_row["last_error_code"] is None
    assert healthy_row["last_refreshed_at"] is not None


def test_patrol_without_stale_connections_calls_no_provider(
    client: TestClient, demo, tmp_path: Path
) -> None:
    db = _db(tmp_path)
    settings = _settings(tmp_path)
    insert_connection(db, last_refreshed_at=datetime.now(UTC) - timedelta(minutes=1))

    assert _run_patrol(client, settings) == 0
    assert demo.token_calls == []
    assert demo.refresh_tokens == 0


def test_patrol_loop_stops_when_event_set(client: TestClient, tmp_path: Path) -> None:
    settings = _settings(tmp_path, refresh_patrol_interval_seconds=1)

    async def _run() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            patrol.run_forever(
                client.app.state.session_factory,
                settings,
                client.app.state.http_transport,
                stop,
            )
        )
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        assert task.done() and not task.cancelled()

    client.portal.call(_run)
