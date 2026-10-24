"""用量聚合与 BYOK 存取(M0-11 本地版)。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from account.main import create_app
from tests.conftest import _IDP, OTHER, TENANT, _settings

INTERNAL = {
    **TENANT,
    "X-Internal-Secret": "internal-secret",
    "X-Tenant-Id": "org-1",
}


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        _settings(
            tmp_path,
            internal_secret="internal-secret",
            byok_secret="byok-secret",
        ),
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def test_usage_summary_empty_and_recorded(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        empty = client.get("/v1/usage/summary", headers=TENANT)
        assert empty.status_code == 200
        assert empty.json() == {
            "today_tokens": 0,
            "month_tokens": 0,
            "today_events": 0,
            "month_events": 0,
            "by_model": [],
        }

        recorded = client.post(
            "/v1/usage/events",
            json={
                "run_id": "r1",
                "model": "deepseek/chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            headers=INTERNAL,
        )
        assert recorded.status_code == 201

        summary = client.get("/v1/usage/summary", headers=TENANT).json()
        assert summary["today_tokens"] == 15
        assert summary["month_tokens"] == 15
        assert summary["today_events"] == 1
        assert summary["month_events"] == 1
        assert summary["by_model"] == [
            {"model": "deepseek/chat", "tokens": 15, "events": 1}
        ]


def test_usage_internal_secret_required(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 1, "completion_tokens": 1},
            headers=TENANT,
        )
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"


def test_byok_upsert_mask_list_delete(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-secret-12345678"},
            headers=TENANT,
        )
        assert created.status_code == 200
        body = created.json()
        assert body["provider"] == "openai"
        assert body["masked_key"] == "••••5678"
        assert "sk-secret" not in str(body)

        listing = client.get("/v1/byok", headers=TENANT).json()
        assert [item["provider"] for item in listing] == ["openai"]
        # 跨租户不可见
        assert client.get("/v1/byok", headers=OTHER).json() == []

        updated = client.put(
            "/v1/byok/openai",
            json={"api_key": "sk-updated-00009999"},
            headers=TENANT,
        )
        assert updated.json()["masked_key"] == "••••9999"

        assert client.delete("/v1/byok/openai", headers=TENANT).status_code == 204
        assert client.get("/v1/byok", headers=TENANT).json() == []


def test_usage_quota_snapshot(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 未配置配额:三个维度都是未限制,已用量 0
        empty = client.get(
            "/v1/usage/quota",
            headers=INTERNAL,
        )
        assert empty.status_code == 200
        assert empty.json() == {
            "monthly_tokens": None,
            "max_concurrent_runs": None,
            "used_tokens": 0,
        }

        updated = client.patch(
            "/v1/organization",
            json={"quota": {"monthly_tokens": 100, "max_concurrent_runs": 2}},
            headers=TENANT,
        )
        assert updated.status_code == 200
        client.post(
            "/v1/usage/events",
            json={"model": "m", "prompt_tokens": 30, "completion_tokens": 10},
            headers=INTERNAL,
        )
        snapshot = client.get(
            "/v1/usage/quota",
            headers=INTERNAL,
        )
    assert snapshot.status_code == 200
    assert snapshot.json() == {
        "monthly_tokens": 100,
        "max_concurrent_runs": 2,
        "used_tokens": 40,
    }


def test_usage_quota_requires_internal_secret(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/v1/usage/quota", headers=TENANT)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.internal_forbidden"
