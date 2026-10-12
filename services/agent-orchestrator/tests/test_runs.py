from fastapi.testclient import TestClient

TENANT_HEADERS = {"X-Tenant-Id": "t-1"}


def _create_run(client: TestClient, tenant: str = "t-1") -> dict:
    body = {
        "title": "整理本周邮件成周报",
        "goal": "汇总本周邮件,生成周报草稿",
        "acceptance_criteria": ["覆盖 5 个工作日", "不超过 300 字"],
    }
    resp = client.post("/api/v1/runs", json=body, headers={"X-Tenant-Id": tenant})
    assert resp.status_code == 201
    return resp.json()


def test_create_and_get_run(client: TestClient) -> None:
    created = _create_run(client)
    assert created["status"] == "pending"
    assert created["rounds"] == 0
    assert len(created["id"]) == 32
    assert created["acceptance_criteria"] == ["覆盖 5 个工作日", "不超过 300 字"]

    got = client.get(f"/api/v1/runs/{created['id']}", headers=TENANT_HEADERS)
    assert got.status_code == 200
    assert got.json()["goal"] == "汇总本周邮件,生成周报草稿"


def test_events_replay_in_seq_order(client: TestClient) -> None:
    created = _create_run(client)
    resp = client.get(f"/api/v1/runs/{created['id']}/events", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["seq"] == 1
    assert events[0]["type"] == "run.created.v1"
    assert len(events[0]["id"]) == 32  # 事件 ID = 信封 ID(幂等去重键)


def test_tenant_isolation(client: TestClient) -> None:
    created = _create_run(client, tenant="t-1")
    resp = client.get(f"/api/v1/runs/{created['id']}", headers={"X-Tenant-Id": "t-2"})
    assert resp.status_code == 404


def test_missing_tenant_header_rejected(client: TestClient) -> None:
    resp = client.post("/api/v1/runs", json={"title": "t", "goal": "g"})
    assert resp.status_code == 422


def test_validation_error_shape(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/runs", json={"title": "", "goal": "g"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422
