"""连接管理:PATCH 开关/展示名、手动刷新、连通性探测、列表过滤。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from tests.conftest import TENANT, DemoMock, insert_connection


def test_patch_connection(client: TestClient, tmp_path: Path) -> None:
    conn = insert_connection(tmp_path / "test.db")
    resp = client.patch(
        f"/v1/connections/{conn['id']}",
        json={"display_name": "我的示例连接器", "enabled": False},
        headers=TENANT,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["display_name"] == "我的示例连接器"
    assert payload["enabled"] is False
    # 关闭启用 != 解绑
    assert payload["status"] == "active"

    empty = client.patch(f"/v1/connections/{conn['id']}", json={}, headers=TENANT)
    assert empty.status_code == 200
    assert empty.json()["display_name"] == "我的示例连接器"


def test_list_filters(client: TestClient, tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    active = insert_connection(db)
    insert_connection(
        db, provider="notion", account="ws-1", status="revoked", enabled=False
    )
    by_provider = client.get(
        "/v1/connections", params={"provider": "notion"}, headers=TENANT
    ).json()
    assert [row["provider"] for row in by_provider] == ["notion"]
    by_status = client.get(
        "/v1/connections", params={"status": "active"}, headers=TENANT
    ).json()
    assert [row["id"] for row in by_status] == [active["id"]]
    disabled = client.get(
        "/v1/connections", params={"enabled": "false"}, headers=TENANT
    ).json()
    assert [row["provider"] for row in disabled] == ["notion"]


def test_refresh_connection(client: TestClient, demo: DemoMock, tmp_path: Path) -> None:
    conn = insert_connection(
        tmp_path / "test.db",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    resp = client.post(f"/v1/connections/{conn['id']}/refresh", headers=TENANT)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "active"
    assert payload["last_refreshed_at"] is not None
    assert demo.refresh_tokens == 1
    assert demo.token_calls[0]["refresh_token"] == "rt-1"


def test_refresh_revoked_connection_conflicts(
    client: TestClient, tmp_path: Path
) -> None:
    conn = insert_connection(tmp_path / "test.db", status="revoked", enabled=False)
    resp = client.post(f"/v1/connections/{conn['id']}/refresh", headers=TENANT)
    assert resp.status_code == 409
    assert resp.json()["code"] == "connector.invalid_state"


def test_refresh_failure_sets_error_code(
    client: TestClient, demo: DemoMock, tmp_path: Path
) -> None:
    conn = insert_connection(tmp_path / "test.db")
    demo.fail["/oauth/token"] = httpx.Response(400, json={"code": 20005})
    resp = client.post(f"/v1/connections/{conn['id']}/refresh", headers=TENANT)
    assert resp.status_code == 409
    assert resp.json()["code"] == "connector.auth_expired"
    detail = client.get(f"/v1/connections/{conn['id']}", headers=TENANT).json()
    assert detail["status"] == "expired"
    assert detail["last_error_code"] == "connector.auth_expired"


def test_check_connection_success_and_failure(
    client: TestClient, demo: DemoMock, tmp_path: Path
) -> None:
    conn = insert_connection(tmp_path / "test.db")
    ok = client.post(f"/v1/connections/{conn['id']}/check", headers=TENANT)
    assert ok.status_code == 200
    assert ok.json()["last_checked_at"] is not None
    assert ok.json()["last_error_code"] is None

    fresh = insert_connection(tmp_path / "test.db", account="u-2")
    demo.fail["/userinfo"] = httpx.Response(401, json={"code": 99991663})
    failed = client.post(f"/v1/connections/{fresh['id']}/check", headers=TENANT)
    assert failed.status_code == 200
    assert failed.json()["status"] == "expired"
    assert failed.json()["last_error_code"] == "connector.auth_expired"
