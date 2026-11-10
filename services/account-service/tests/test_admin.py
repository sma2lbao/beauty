"""管理面验收(P0-7,admin-plane §7):RBAC、账号管理、审计。

装配与 conftest 一致(TestIdP 真签 token + 临时 sqlite 库),但这里需要
ADMIN_EMAILS 与 internal_secret,故本文件自建 client fixture。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from common.auth import JwtVerifier
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app
from tests.conftest import _register_account

ADMIN_EMAIL = "root@example.com"
ADMIN2_EMAIL = "root2@example.com"
INTERNAL_SECRET = "test-internal"
PASSWORD = "hunter2ok"
GHOST_ID = "f" * 32


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """真签发链路(本 app 签名 + 本 app JWKS 验签),与 test_account.py 一致。"""
    settings = Settings(
        _env_file=None,
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
        admin_emails=[ADMIN_EMAIL, ADMIN2_EMAIL],
        internal_secret=INTERNAL_SECRET,
    )
    app = create_app(settings)
    verifier = JwtVerifier(
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        transport=httpx.ASGITransport(app=app),
    )
    app.state.token_verifier = verifier
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(verifier.aclose())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict:
    return _register_account(client, email=email, password=PASSWORD)


def _admin_headers(client: TestClient, email: str = ADMIN_EMAIL) -> dict[str, str]:
    return _auth(_register(client, email)["access_token"])


def _internal_headers(account_id: str) -> dict[str, str]:
    return {"X-Internal-Secret": INTERNAL_SECRET, "X-Account-Id": account_id}


def test_admin_endpoints_require_bearer_token(client: TestClient) -> None:
    response = client.get("/v1/admin/accounts")
    assert response.status_code == 401


def test_non_admin_is_denied_and_denial_is_audited(client: TestClient) -> None:
    alice = _register(client, "alice@example.com")

    denied = client.get("/v1/admin/accounts", headers=_auth(alice["access_token"]))
    assert denied.status_code == 403
    assert denied.json()["code"] == "admin.required"

    root = _admin_headers(client)
    logs = client.get(
        "/v1/admin/audit-logs",
        params={"action": "admin.access.denied"},
        headers=root,
    )
    assert logs.status_code == 200
    body = logs.json()
    assert body["total"] == 1
    entry = body["items"][0]
    assert entry["actor_account_id"] == alice["account_id"]
    assert entry["actor_email"] == "alice@example.com"
    assert entry["action"] == "admin.access.denied"
    assert entry["target_type"] == "endpoint"
    assert entry["target_id"] == "/v1/admin/accounts"
    assert entry["status"] == "denied"
    assert entry["detail"] == {"method": "GET", "required": "admin"}
    assert entry["request_id"]
    assert "admin.access.denied" in {
        item["action"]
        for item in client.get("/v1/admin/audit-logs", headers=root).json()["items"]
    }


def test_admin_lists_accounts_with_usage(client: TestClient) -> None:
    _register(client, "alice@example.com")
    headers = _admin_headers(client)

    response = client.get("/v1/admin/accounts", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    by_email = {item["email"]: item for item in body["items"]}
    alice = by_email["alice@example.com"]
    assert alice["role"] == "user"
    assert alice["status"] == "active"
    assert alice["quota"] == {}
    assert alice["created_at"]
    assert set(alice["usage"]) == {
        "month_tokens",
        "month_events",
        "month_cost_usd",
        "unpriced_events",
    }
    assert by_email[ADMIN_EMAIL]["role"] == "admin"


def test_env_admin_emails_persist_admin_role_at_register(client: TestClient) -> None:
    root = _register(client, ADMIN_EMAIL)
    me = client.get("/v1/me", headers=_auth(root["access_token"]))
    assert me.status_code == 200
    assert me.json()["role"] == "admin"


def test_admin_email_fallback_after_role_demoted(client: TestClient) -> None:
    root = _register(client, ADMIN_EMAIL)
    root2 = _register(client, ADMIN2_EMAIL)

    demoted = client.patch(
        f"/v1/admin/accounts/{root['account_id']}/role",
        json={"role": "user"},
        headers=_auth(root2["access_token"]),
    )
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"

    # role 已是 user,但邮箱仍在 ADMIN_EMAILS:require_admin 兜底放行
    still_admin = client.get("/v1/admin/accounts", headers=_auth(root["access_token"]))
    assert still_admin.status_code == 200
    me = client.get("/v1/me", headers=_auth(root["access_token"]))
    assert me.json()["role"] == "user"


def test_login_persists_admin_role_from_env_list(client: TestClient) -> None:
    root = _register(client, ADMIN_EMAIL)
    root2 = _register(client, ADMIN2_EMAIL)
    client.patch(
        f"/v1/admin/accounts/{root['account_id']}/role",
        json={"role": "user"},
        headers=_auth(root2["access_token"]),
    )

    login = client.post(
        "/v1/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD}
    )
    assert login.status_code == 200
    me = client.get("/v1/me", headers=_auth(login.json()["access_token"]))
    assert me.json()["role"] == "admin"


def test_list_filters_and_pagination(client: TestClient) -> None:
    _register(client, "alice@example.com")
    bob = _register(client, "bob@example.com")
    client.patch(
        "/v1/me",
        json={"display_name": "月亮 Moon"},
        headers=_auth(bob["access_token"]),
    )
    root = _admin_headers(client)

    by_display_name = client.get(
        "/v1/admin/accounts", params={"q": "MOON"}, headers=root
    )
    assert [item["email"] for item in by_display_name.json()["items"]] == [
        "bob@example.com"
    ]
    by_email = client.get("/v1/admin/accounts", params={"q": "ALICE@"}, headers=root)
    assert [item["email"] for item in by_email.json()["items"]] == ["alice@example.com"]

    disabled = client.patch(
        f"/v1/admin/accounts/{bob['account_id']}/status",
        json={"status": "disabled"},
        headers=root,
    )
    assert disabled.status_code == 200
    only_disabled = client.get(
        "/v1/admin/accounts", params={"status": "disabled"}, headers=root
    )
    assert only_disabled.json()["total"] == 1
    assert [item["email"] for item in only_disabled.json()["items"]] == [
        "bob@example.com"
    ]

    page = client.get(
        "/v1/admin/accounts", params={"limit": 1, "offset": 1}, headers=root
    )
    assert len(page.json()["items"]) == 1
    assert page.json()["total"] == 3

    assert (
        client.get("/v1/admin/accounts", params={"limit": 0}, headers=root).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/admin/accounts", params={"offset": -1}, headers=root
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/v1/admin/accounts", params={"status": "banned"}, headers=root
        ).status_code
        == 422
    )


def test_usage_aggregation_in_list_and_detail(client: TestClient) -> None:
    alice = _register(client, "alice@example.com")
    internal = _internal_headers(alice["account_id"])
    first = client.post(
        "/v1/usage/events",
        json={"model": "gpt-x", "prompt_tokens": 100, "cost_usd": 0.5},
        headers=internal,
    )
    assert first.status_code == 201
    second = client.post(
        "/v1/usage/events",
        json={"model": "gpt-x", "prompt_tokens": 50},
        headers=internal,
    )
    assert second.status_code == 201

    root = _admin_headers(client)
    listed = client.get(
        "/v1/admin/accounts", params={"q": "alice@example.com"}, headers=root
    ).json()["items"][0]
    assert listed["usage"] == {
        "month_tokens": 150,
        "month_events": 2,
        "month_cost_usd": 0.5,
        "unpriced_events": 1,
    }

    detail = client.get(f"/v1/admin/accounts/{alice['account_id']}", headers=root)
    assert detail.status_code == 200
    body = detail.json()
    assert body["account"]["id"] == alice["account_id"]
    assert body["usage"]["month_tokens"] == 150
    assert body["usage"]["month_events"] == 2
    assert body["usage"]["month_cost_usd"] == 0.5
    assert body["usage"]["unpriced_events"] == 1
    assert body["usage"]["by_model"][0]["model"] == "gpt-x"


def test_quota_patch_set_partial_clear_and_validation(client: TestClient) -> None:
    alice = _register(client, "alice@example.com")
    root = _admin_headers(client)
    url = f"/v1/admin/accounts/{alice['account_id']}/quota"

    both = client.patch(
        url,
        json={"monthly_tokens": 1000, "max_concurrent_runs": 2},
        headers=root,
    )
    assert both.status_code == 200
    assert both.json()["quota"] == {
        "monthly_tokens": 1000,
        "max_concurrent_runs": 2,
    }

    partial = client.patch(url, json={"monthly_tokens": 2000}, headers=root)
    assert partial.json()["quota"] == {
        "monthly_tokens": 2000,
        "max_concurrent_runs": 2,
    }

    cleared = client.patch(url, json={"monthly_tokens": None}, headers=root)
    assert cleared.json()["quota"] == {"max_concurrent_runs": 2}
    quota = client.get(
        "/v1/usage/quota/me", headers=_auth(alice["access_token"])
    ).json()
    assert quota["max_concurrent_runs"] == 2
    assert quota["monthly_tokens"] is None

    assert (
        client.patch(url, json={"monthly_tokens": 0}, headers=root).status_code == 422
    )
    assert (
        client.patch(url, json={"max_concurrent_runs": -1}, headers=root).status_code
        == 422
    )
    assert client.patch(url, json={}, headers=root).status_code == 422
    assert client.patch(url, json={"unknown": 1}, headers=root).status_code == 422

    missing = client.patch(
        f"/v1/admin/accounts/{GHOST_ID}/quota",
        json={"monthly_tokens": 1},
        headers=root,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "account.account_not_found"


def test_role_patch_takes_effect_and_self_change_is_conflict(
    client: TestClient,
) -> None:
    alice = _register(client, "alice@example.com")
    root = _admin_headers(client)
    url = f"/v1/admin/accounts/{alice['account_id']}/role"

    promoted = client.patch(url, json={"role": "admin"}, headers=root)
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    # 角色在库里、每请求判定:提升后立刻可用
    assert (
        client.get(
            "/v1/admin/accounts", headers=_auth(alice["access_token"])
        ).status_code
        == 200
    )

    demoted = client.patch(url, json={"role": "user"}, headers=root)
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"
    assert (
        client.get(
            "/v1/admin/accounts", headers=_auth(alice["access_token"])
        ).status_code
        == 403
    )

    root_id = client.get("/v1/me", headers=root).json()["id"]
    self_change = client.patch(
        f"/v1/admin/accounts/{root_id}/role", json={"role": "admin"}, headers=root
    )
    assert self_change.status_code == 409
    assert self_change.json()["code"] == "admin.self_role"

    assert client.patch(url, json={"role": "owner"}, headers=root).status_code == 422
    assert (
        client.patch(
            f"/v1/admin/accounts/{GHOST_ID}/role", json={"role": "admin"}, headers=root
        ).status_code
        == 404
    )


def test_disable_revokes_sessions_and_refresh_tokens(client: TestClient) -> None:
    alice = _register(client, "alice@example.com")
    alice_headers = _auth(alice["access_token"])
    root = _admin_headers(client)
    created = client.post(
        "/v1/sessions", json={"device": "phone"}, headers=alice_headers
    )
    assert created.status_code == 201
    # 提升为 admin:验证停用判定优先于角色
    client.patch(
        f"/v1/admin/accounts/{alice['account_id']}/role",
        json={"role": "admin"},
        headers=root,
    )
    assert client.get("/v1/admin/accounts", headers=alice_headers).status_code == 200

    status_url = f"/v1/admin/accounts/{alice['account_id']}/status"
    disabled = client.patch(status_url, json={"status": "disabled"}, headers=root)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    refreshed = client.post(
        "/v1/auth/refresh", json={"refresh_token": alice["refresh_token"]}
    )
    assert refreshed.status_code == 401

    sessions = client.get("/v1/sessions", headers=alice_headers)
    assert sessions.status_code == 200
    assert sessions.json()
    assert all(item["status"] == "revoked" for item in sessions.json())

    denied = client.get("/v1/admin/accounts", headers=alice_headers)
    assert denied.status_code == 403
    assert denied.json()["code"] == "admin.account_disabled"

    root_id = client.get("/v1/me", headers=root).json()["id"]
    self_disable = client.patch(
        f"/v1/admin/accounts/{root_id}/status",
        json={"status": "disabled"},
        headers=root,
    )
    assert self_disable.status_code == 409
    assert self_disable.json()["code"] == "admin.self_disable"

    enabled = client.patch(status_url, json={"status": "active"}, headers=root)
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"
    login = client.post(
        "/v1/auth/login", json={"email": "alice@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200


def test_write_actions_are_audited_and_reads_are_not(client: TestClient) -> None:
    alice = _register(client, "alice@example.com")
    root = _admin_headers(client)
    root_id = client.get("/v1/me", headers=root).json()["id"]

    baseline = client.get("/v1/admin/audit-logs", headers=root).json()["total"]
    client.get("/v1/admin/accounts", headers=root)
    client.get(f"/v1/admin/accounts/{alice['account_id']}", headers=root)
    client.get("/v1/admin/audit-logs", headers=root)
    assert client.get("/v1/admin/audit-logs", headers=root).json()["total"] == baseline

    url = f"/v1/admin/accounts/{alice['account_id']}"
    client.patch(f"{url}/quota", json={"monthly_tokens": 500}, headers=root)
    client.patch(f"{url}/role", json={"role": "admin"}, headers=root)
    client.patch(f"{url}/status", json={"status": "disabled"}, headers=root)

    entries = client.get(
        "/v1/admin/audit-logs",
        params={"target_id": alice["account_id"], "limit": 100},
        headers=root,
    ).json()["items"]
    assert {item["action"] for item in entries} == {
        "account.quota.update",
        "account.role.update",
        "account.status.update",
    }
    by_action = {item["action"]: item for item in entries}
    quota_entry = by_action["account.quota.update"]
    assert quota_entry["actor_account_id"] == root_id
    assert quota_entry["actor_email"] == ADMIN_EMAIL
    assert quota_entry["target_type"] == "account"
    assert quota_entry["status"] == "success"
    assert quota_entry["request_id"]
    assert quota_entry["detail"] == {"before": {}, "after": {"monthly_tokens": 500}}
    assert by_action["account.role.update"]["detail"] == {
        "before": "user",
        "after": "admin",
    }
    status_detail = by_action["account.status.update"]["detail"]
    assert status_detail["before"] == "active"
    assert status_detail["after"] == "disabled"
    assert status_detail["revoked_refresh_tokens"] >= 1
    assert status_detail["revoked_sessions"] >= 1

    filtered = client.get(
        "/v1/admin/audit-logs",
        params={"action": "account.role.update", "actor_account_id": root_id},
        headers=root,
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["action"] == "account.role.update"


def test_unknown_account_detail_is_404(client: TestClient) -> None:
    root = _admin_headers(client)
    response = client.get(f"/v1/admin/accounts/{GHOST_ID}", headers=root)
    assert response.status_code == 404
    assert response.json()["code"] == "account.account_not_found"
