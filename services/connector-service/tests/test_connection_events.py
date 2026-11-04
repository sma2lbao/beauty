"""连接生命周期事件:同事务写 outbox、关闭时降级日志、dispatcher 能投递。

设计 §5.5/§8.2(C1.10)的三个验收点各一条:created/revoked/expired 各落一行、
outbox 关闭只记日志不写库、dispatcher.drain_once 把行投给总线处理器。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from events.bus import InMemoryEventBus
from events.outbox import DISPATCHED, OutboxDispatcher
from fastapi.testclient import TestClient

from connector.providers.base import Credentials, Identity
from connector.providers.errors import ProviderError
from connector.services import connections as connections_service
from connector.services import events as events_service
from tests.conftest import _settings, feishu_credentials, insert_connection

_EVENT_TYPE = {
    "connection.created": events_service.CONNECTION_CREATED,
    "connection.expired": events_service.CONNECTION_EXPIRED,
    "connection.revoked": events_service.CONNECTION_REVOKED,
}


def _db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _outbox_rows(db_path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as raw:
        raw.row_factory = sqlite3.Row
        rows = raw.execute("SELECT * FROM outbox_events").fetchall()
    return [dict(row) for row in rows]


def _connection_row(db_path: Path, connection_id: str) -> dict[str, Any]:
    with sqlite3.connect(db_path) as raw:
        raw.row_factory = sqlite3.Row
        row = raw.execute(
            "SELECT status, last_error_code FROM connections WHERE id = ?",
            (connection_id,),
        ).fetchone()
    return dict(row)


def _authorize(session, settings, *, account: str = "ou_new"):
    """走真实的授权落库路径(新建行 -> 发 created)。"""
    return connections_service.upsert_authorized_connection(
        session,
        settings=settings,
        tenant_id="t-1",
        provider_name="feishu",
        owner_subject="u2",
        credentials=Credentials(
            access_token="at-9",
            refresh_token="rt-9",
            scope="offline_access",
        ),
        identity=Identity(account=account, display_name="李四"),
        scopes=["offline_access"],
    )


def test_created_event_written_with_connection_in_same_transaction(
    client: TestClient, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, outbox_enabled=True)

    async def _run() -> str:
        async with client.app.state.session_factory() as session:
            conn = await _authorize(session, settings)
            await session.commit()
            return conn.id

    connection_id = client.portal.call(_run)

    rows = _outbox_rows(_db(tmp_path))
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == _EVENT_TYPE["connection.created"]
    assert row["tenant_id"] == "t-1"
    assert row["envelope_tenant_id"] == "t-1"
    assert row["status"] == "pending"
    assert row["attempts"] == 0
    assert json.loads(row["payload"]) == {
        "connection_id": connection_id,
        "provider": "feishu",
        "account": "ou_new",
        "status": "active",
        "owner_subject": "u2",
    }


def test_created_event_rolls_back_with_connection(
    client: TestClient, tmp_path: Path
) -> None:
    """事件与状态同事务:回滚时 outbox 行也不能留下。"""
    settings = _settings(tmp_path, outbox_enabled=True)

    async def _run() -> None:
        async with client.app.state.session_factory() as session:
            await _authorize(session, settings)
            await session.rollback()

    client.portal.call(_run)
    assert _outbox_rows(_db(tmp_path)) == []


def test_revoked_event_written(client: TestClient, tmp_path: Path) -> None:
    conn = insert_connection(_db(tmp_path))
    settings = _settings(tmp_path, outbox_enabled=True)

    async def _run() -> str:
        async with client.app.state.session_factory() as session:
            revoked = await connections_service.revoke_connection(
                session, settings, "t-1", conn["id"]
            )
            await session.commit()
            return revoked.status

    assert client.portal.call(_run) == "revoked"
    rows = _outbox_rows(_db(tmp_path))
    assert [row["event_type"] for row in rows] == [_EVENT_TYPE["connection.revoked"]]
    payload = json.loads(rows[0]["payload"])
    assert payload["connection_id"] == conn["id"]
    assert payload["status"] == "revoked"
    assert payload["owner_subject"] == "u1"


def test_expired_event_written_on_reauth_failure(
    client: TestClient, tmp_path: Path
) -> None:
    """refresh 因 invalid_grant 类错误失败:置 expired 的同时一条 expired 事件。"""
    conn = insert_connection(
        _db(tmp_path), credentials=feishu_credentials(refresh_token=None)
    )
    settings = _settings(tmp_path, outbox_enabled=True)

    async def _run() -> str:
        async with client.app.state.session_factory() as session:
            with pytest.raises(ProviderError) as raised:
                await connections_service.refresh_connection(
                    session, settings, "t-1", conn["id"], None
                )
            await session.commit()  # 模拟 API 端点:先把失败留痕提交再抛
            return raised.value.code

    assert client.portal.call(_run) == "connector.auth_expired"
    rows = _outbox_rows(_db(tmp_path))
    assert [row["event_type"] for row in rows] == [_EVENT_TYPE["connection.expired"]]
    payload = json.loads(rows[0]["payload"])
    assert payload["connection_id"] == conn["id"]
    assert payload["status"] == "expired"

    stored = _connection_row(_db(tmp_path), conn["id"])
    assert stored["status"] == "expired"
    assert stored["last_error_code"] == "connector.auth_expired"


def test_disabled_outbox_only_logs(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """outbox 关闭(默认):不写行、只留结构化日志,调用方无须分支。"""
    db = _db(tmp_path)
    settings = _settings(tmp_path)  # outbox_enabled=False
    logged: list[dict[str, Any]] = []

    class _FakeLogger:
        def info(self, event: str, **fields: Any) -> None:
            logged.append({"event": event, **fields})

    monkeypatch.setattr(events_service, "_log", _FakeLogger())

    async def _run() -> str:
        async with client.app.state.session_factory() as session:
            conn = await _authorize(session, settings)
            await session.commit()
            return conn.id

    connection_id = client.portal.call(_run)

    assert _outbox_rows(db) == []
    assert len(logged) == 1
    assert logged[0]["event"] == "connection_event"
    assert logged[0]["event_type"] == _EVENT_TYPE["connection.created"]
    assert logged[0]["connection_id"] == connection_id
    assert logged[0]["tenant_id"] == "t-1"


def test_dispatcher_delivers_recorded_event(
    client: TestClient, tmp_path: Path
) -> None:
    """dispatcher 把 outbox 行投给总线处理器,并把行标记为 dispatched。"""
    settings = _settings(tmp_path, outbox_enabled=True)

    async def _run() -> str:
        async with client.app.state.session_factory() as session:
            conn = await _authorize(session, settings)
            await session.commit()
            return conn.id

    connection_id = client.portal.call(_run)

    received: list[Any] = []

    async def _handler(envelope) -> None:
        received.append(envelope)

    bus = InMemoryEventBus()
    bus.subscribe("#", _handler)
    dispatcher = OutboxDispatcher(
        client.app.state.session_factory, bus, batch_size=10, max_attempts=2
    )
    stats = client.portal.call(dispatcher.drain_once)

    assert (stats.claimed, stats.delivered, stats.failed) == (1, 1, 0)
    assert len(received) == 1
    envelope = received[0]
    assert envelope.type == _EVENT_TYPE["connection.created"]
    assert envelope.payload["connection_id"] == connection_id
    assert envelope.tenant_id == "t-1"
    assert _outbox_rows(_db(tmp_path))[0]["status"] == DISPATCHED
