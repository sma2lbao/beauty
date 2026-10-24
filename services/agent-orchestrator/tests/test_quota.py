"""P0-2 配额准入 + P0-3 token 预算的边界行为。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import quota
from tests.conftest import _IDP, TENANT, FakeLLM, _client, _settings


def _quota_client(payload: dict[str, Any] | None, *, error: bool = False):
    """把配额快照注入进程级客户端(MockTransport,零网络)。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if error:
            raise httpx.ConnectError("account down")
        return httpx.Response(200, json=payload or {})

    quota.configure_client(
        base_url="http://account.test",
        internal_secret="s3cret",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture(autouse=True)
def _reset_quota_and_budget():
    yield
    quota.configure_client(base_url="", internal_secret="")


def _create(client) -> Any:
    return client.post(
        "/api/v1/runs",
        json={
            "title": "整理周报",
            "goal": "把本周日程整理成周报",
            "acceptance_criteria": ["日程已整理"],
        },
        headers=TENANT,
    )


def test_monthly_token_quota_blocks_new_run(tmp_path) -> None:
    _quota_client(
        {"monthly_tokens": 1000, "max_concurrent_runs": None, "used_tokens": 1000}
    )
    fake = FakeLLM(["# 计划"])
    with _client(tmp_path, fake) as client:
        response = _create(client)

    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "quota.monthly_tokens_exceeded"
    assert body["details"] == {"limit": 1000, "used": 1000}


def test_monthly_quota_allows_run_below_limit(tmp_path) -> None:
    _quota_client(
        {"monthly_tokens": 1000, "max_concurrent_runs": None, "used_tokens": 999}
    )
    fake = FakeLLM(["# 计划"])
    with _client(tmp_path, fake) as client:
        assert _create(client).status_code == 201


def test_concurrent_run_quota(tmp_path) -> None:
    _quota_client({"monthly_tokens": None, "max_concurrent_runs": 1, "used_tokens": 0})
    fake = FakeLLM(["# 计划"])
    with _client(tmp_path, fake) as client:
        first = _create(client)
        assert first.status_code == 201
        second = _create(client)

    assert second.status_code == 429
    body = second.json()
    assert body["code"] == "quota.concurrent_runs_exceeded"
    assert body["details"] == {"limit": 1, "active": 1}


def test_quota_failure_fails_open(tmp_path) -> None:
    """account 不可用时不能连带打死建 run(限流不是鉴权)。"""
    _quota_client(None, error=True)
    fake = FakeLLM(["# 计划"])
    with _client(tmp_path, fake) as client:
        assert _create(client).status_code == 201


def test_no_quota_client_means_unlimited(tmp_path) -> None:
    fake = FakeLLM(["# 计划"])
    with _client(tmp_path, fake) as client:
        assert _create(client).status_code == 201


def _budget_client(tmp_path, tokens: int):
    """按 settings 装配预算的客户端(lifespan 会用 settings 覆盖进程级预算)。"""
    from tests.test_usage import UsageLLM

    fake = FakeLLM(
        [
            "# 计划",
            "执行结果",
            '{"summary": "通过", "verdicts": {"日程已整理": true}}',
            "# 交付物",
        ]
    )
    app = create_app(
        _settings(tmp_path, "budget.db", llm_enabled=True, run_token_budget=tokens),
        llm_client=UsageLLM(fake, tokens=(10, 5)),
        token_verifier=_IDP.verifier(),
    )
    return TestClient(app)


def test_token_budget_stops_run(tmp_path) -> None:
    """每次模型调用记 15 token、预算 20 → deliver 前中止,run failed。"""
    with _budget_client(tmp_path, 20) as client:
        run_id = _create(client).json()["id"]
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        # 放行后 execute→verify→deliver 在同一段图调用里继续,预算在节点边界拦下
        client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT, json={})
        run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()

    assert run["status"] == "failed"
    assert "TokenBudgetExceeded" in (run["status_reason"] or "")
    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    assert any(event["type"] == "run.failed.v1" for event in events)


def test_budget_zero_does_not_limit(tmp_path) -> None:
    with _budget_client(tmp_path, 0) as client:
        run_id = _create(client).json()["id"]
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT, json={})
        run = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()
    assert run["status"] == "completed"
