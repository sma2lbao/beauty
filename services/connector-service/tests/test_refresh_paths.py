"""刷新/兜底分支的服务层单测:覆盖行锁二次检查、瞬时错误与凭证边界。

API 层测试(``test_connection_actions.py``)只走 HTTP,拿不到"等锁期间已被刷新"
这类时序分支,所以这里直接以 ``client.app.state.session_factory`` 开事务,
并用 TestClient 的 portal 在同一个事件循环里执行,对 service 层做精确断言。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from connector.models.connections import Connection
from connector.providers.base import Credentials, Identity
from connector.repositories import ConnectionRepository
from connector.services import connections as connections_service
from connector.services import execute as execute_service
from connector.services.connections import (
    refresh_if_expiring,
    upsert_authorized_connection,
)
from tests.conftest import TENANT, _settings, feishu_credentials, insert_connection


def _db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def test_refresh_skips_when_not_expiring(
    client: TestClient, feishu, tmp_path: Path
) -> None:
    conn = insert_connection(
        _db(tmp_path), credentials=feishu_credentials(expires_in=7200)
    )
    settings = _settings(tmp_path)

    async def _run() -> str:
        factory = client.app.state.session_factory
        async with factory() as session:
            row = await ConnectionRepository(session).require_for_tenant(
                "t-1", conn["id"]
            )
            refreshed = await refresh_if_expiring(session, settings, "t-1", row, None)
            return refreshed.id

    assert client.portal.call(_run) == conn["id"]
    assert feishu.refresh_tokens == 0


def test_refresh_rechecks_under_lock(
    client: TestClient, feishu, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """等锁期间别的请求已刷新:锁内二次检查放行,不再换 token。"""
    conn = insert_connection(
        _db(tmp_path), expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    settings = _settings(tmp_path)
    calls = iter([True, False])
    monkeypatch.setattr(
        connections_service, "is_expiring", lambda *args, **kwargs: next(calls)
    )

    async def _run() -> str:
        factory = client.app.state.session_factory
        async with factory() as session:
            row = await ConnectionRepository(session).require_for_tenant(
                "t-1", conn["id"]
            )
            refreshed = await refresh_if_expiring(session, settings, "t-1", row, None)
            return refreshed.id

    assert client.portal.call(_run) == conn["id"]
    assert feishu.refresh_tokens == 0


def test_refresh_returns_inactive_connection_under_lock(
    client: TestClient, feishu, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = insert_connection(
        _db(tmp_path),
        status="expired",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    settings = _settings(tmp_path)
    monkeypatch.setattr(connections_service, "is_expiring", lambda *a, **k: True)

    async def _run() -> tuple[str, str]:
        factory = client.app.state.session_factory
        async with factory() as session:
            row = await ConnectionRepository(session).require_for_tenant(
                "t-1", conn["id"]
            )
            refreshed = await refresh_if_expiring(session, settings, "t-1", row, None)
            return refreshed.id, refreshed.status

    assert client.portal.call(_run) == (conn["id"], "expired")
    assert feishu.refresh_tokens == 0


def test_transient_refresh_failure_keeps_connection_active(
    client: TestClient, feishu, tmp_path: Path
) -> None:
    conn = insert_connection(
        _db(tmp_path), expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    feishu.fail["/oauth/v3/token"] = httpx.Response(
        429,
        headers={"x-ogw-ratelimit-reset": "7"},
        json={"code": 99991400, "msg": "rate limited"},
    )
    response = client.post(f"/v1/connections/{conn['id']}/refresh", headers=TENANT)
    assert response.status_code == 429
    assert response.json()["code"] == "connector.upstream_rate_limited"

    fresh = client.get(f"/v1/connections/{conn['id']}", headers=TENANT).json()
    assert fresh["status"] == "active"
    assert fresh["last_error_code"] == "connector.upstream_rate_limited"


def test_upstream_outage_refresh_failure_keeps_connection_active(
    client: TestClient, feishu, tmp_path: Path
) -> None:
    """token 端点 5xx 是瞬时故障:不许置 expired 逼用户重连。"""
    conn = insert_connection(
        _db(tmp_path), expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    feishu.fail["/oauth/v3/token"] = httpx.Response(503, text="upstream down")
    response = client.post(f"/v1/connections/{conn['id']}/refresh", headers=TENANT)
    assert response.status_code == 502
    assert response.json()["code"] == "connector.upstream_unavailable"

    fresh = client.get(f"/v1/connections/{conn['id']}", headers=TENANT).json()
    assert fresh["status"] == "active"
    assert fresh["last_error_code"] == "connector.upstream_unavailable"


def test_apply_credentials_keeps_scopes_when_scope_empty() -> None:
    conn = Connection(
        tenant_id="t-1", provider="feishu", account="ou_1", scopes=["old"]
    )
    credentials = Credentials(
        access_token="at-x",
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )

    class _Settings:
        credential_secret = "dev-connector-secret-change-me"

    connections_service._apply_credentials(conn, _Settings(), credentials)
    assert conn.scopes == ["old"]
    assert conn.expires_at is not None


def test_upsert_reuses_existing_connection(
    client: TestClient, feishu, tmp_path: Path
) -> None:
    conn = insert_connection(_db(tmp_path))
    settings = _settings(tmp_path)

    async def _run() -> tuple[str, int]:
        factory = client.app.state.session_factory
        async with factory() as session:
            merged = await upsert_authorized_connection(
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
                identity=Identity(account="ou_1", display_name="李四"),
                scopes=["offline_access"],
            )
            await session.commit()
            rows = await ConnectionRepository(session).list_filtered("t-1")
            return merged.id, len(rows)

    assert client.portal.call(_run) == (conn["id"], 1)


def test_execute_wraps_http_error(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    insert_connection(_db(tmp_path))
    settings = _settings(tmp_path)

    class _Boom:
        name = "feishu"
        display_name = "飞书"

        def tool_spec(self, name: str) -> object:
            return object()

        async def call(self, *args, **kwargs):
            raise httpx.ConnectError("boom")

    # 执行面改为按租户解析 provider(tenant_apps 优先),桩点随之移动
    async def _provider_for_tenant(*args, **kwargs):
        return _Boom()

    monkeypatch.setattr(
        execute_service, "provider_for_tenant", _provider_for_tenant
    )

    async def _run() -> dict:
        factory = client.app.state.session_factory
        async with factory() as session:
            payload = await execute_service.execute_tool(
                session,
                settings,
                tenant_id="t-1",
                provider_name="feishu",
                tool_name="Feishu__get_me",
                arguments={},
                transport=None,
            )
            await session.commit()
            return payload

    payload = client.portal.call(_run)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "connector.upstream_unavailable"
    assert payload["error"]["retryable"] is True


def test_check_transient_failure_keeps_active(
    client: TestClient, feishu, tmp_path: Path
) -> None:
    conn = insert_connection(_db(tmp_path))
    feishu.fail["/authen/v1/user_info"] = httpx.Response(
        429,
        headers={"x-ogw-ratelimit-reset": "3"},
        json={"code": 99991400, "msg": "rate limited"},
    )
    response = client.post(f"/v1/connections/{conn['id']}/check", headers=TENANT)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["last_error_code"] == "connector.upstream_rate_limited"


def test_check_unknown_identity_shape_is_not_auth_failure(
    client: TestClient, feishu, tmp_path: Path
) -> None:
    """上游返回结构调整不该判成"需要重新授权",只留错误码。"""
    conn = insert_connection(_db(tmp_path))
    feishu.fail["/authen/v1/user_info"] = httpx.Response(
        200, json={"code": 0, "data": {}}
    )
    response = client.post(f"/v1/connections/{conn['id']}/check", headers=TENANT)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "active"
    assert body["last_error_code"] == "connector.upstream_error"
