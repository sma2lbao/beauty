"""账号域:组织(幂等建档/配额)、成员、会话。"""

from fastapi.testclient import TestClient

from tests.conftest import TENANT, _create_user

OTHER = {"X-Tenant-Id": "org-2"}


def test_organization_is_created_idempotently(client: TestClient) -> None:
    first = client.get("/v1/organization", headers=TENANT)
    assert first.status_code == 200
    body = first.json()
    assert body["id"] == "org-1"  # 组织 id 即租户标识
    assert body["plan"] == "free"
    assert body["quota"] == {}

    again = client.get("/v1/organization", headers=TENANT).json()
    # 幂等:不会重复建(created_at 为同一时刻;sqlite 读回不带 tzinfo,
    # 刚建的对象带 Z——这里比较时刻本身,Postgres 上两者都带时区)
    assert again["created_at"].removesuffix("Z") == body["created_at"].removesuffix("Z")


def test_organization_update_plan_and_quota(client: TestClient) -> None:
    patched = client.patch(
        "/v1/organization",
        json={"name": "示例科技", "plan": "team", "quota": {"seats": 20}},
        headers=TENANT,
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "示例科技"
    assert body["plan"] == "team"
    assert body["quota"] == {"seats": 20}

    assert client.get("/v1/organization", headers=OTHER).json()["plan"] == "free"


def test_users_create_list_get_and_conflict(client: TestClient) -> None:
    created = _create_user(client)
    assert created.status_code == 201
    user = created.json()
    assert user["role"] == "owner"
    assert user["status"] == "active"

    duplicate = _create_user(client)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "account.user_exists"

    listed = client.get("/v1/users", headers=TENANT).json()
    assert [item["id"] for item in listed] == [user["id"]]

    fetched = client.get(f"/v1/users/{user['id']}", headers=TENANT).json()
    assert fetched["subject"] == "idp|u1"


def test_user_update_disable_and_isolation(client: TestClient) -> None:
    user_id = _create_user(client).json()["id"]

    patched = client.patch(
        f"/v1/users/{user_id}",
        json={"display_name": "小月", "status": "disabled"},
        headers=TENANT,
    ).json()
    assert patched["display_name"] == "小月"
    assert patched["status"] == "disabled"

    # 别租户看不到(404 而不是 403,避免泄漏存在性)
    assert client.get(f"/v1/users/{user_id}", headers=OTHER).status_code == 404
    assert client.get("/v1/users", headers=OTHER).json() == []
    assert client.get("/v1/users/nope", headers=TENANT).json()["code"] == (
        "account.user_not_found"
    )


def test_sessions_register_list_and_revoke(client: TestClient) -> None:
    user_id = _create_user(client).json()["id"]

    created = client.post(
        "/v1/sessions",
        json={"user_id": user_id, "device": "MacBook / Chrome"},
        headers=TENANT,
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    assert len(client.get("/v1/sessions", headers=TENANT).json()) == 1

    revoked = client.post(f"/v1/sessions/{session_id}/revoke", headers=TENANT).json()
    assert revoked["status"] == "revoked"

    again = client.post(f"/v1/sessions/{session_id}/revoke", headers=TENANT)
    assert again.status_code == 409
    assert again.json()["code"] == "account.session_revoked"


def test_session_requires_existing_user(client: TestClient) -> None:
    resp = client.post(
        "/v1/sessions",
        json={"user_id": "nope", "device": "iPad"},
        headers=TENANT,
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "account.user_not_found"
