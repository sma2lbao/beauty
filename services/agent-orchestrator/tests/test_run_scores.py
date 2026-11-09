"""run 评分:人工点赞/点踩往返、规则基线、Langfuse 上报调度。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from common import langfuse
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import run_scores
from agent_orchestrator.services.post_commit import (
    PostCommitRuntime,
    bind_runtime,
    drain_after_commit,
)
from tests.conftest import (
    _IDP,
    ACCOUNT,
    OTHER,
    _create_run,
    _settings,
)

TRACE_ID = "0123456789abcdef0123456789abcdef"


def _score_client(tmp_path) -> TestClient:
    """Langfuse 启用但 tracing 关闭:只验证评分事件与上报登记,零网络。"""
    settings = _settings(
        tmp_path,
        "scores.db",
        tracing_enabled=False,
        langfuse_enabled=True,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
    )
    app = create_app(settings, token_verifier=_IDP.verifier())
    return TestClient(app)


def _events(client: TestClient, run_id: str) -> list[dict[str, Any]]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT)
    assert resp.status_code == 200
    return resp.json()


def test_human_score_roundtrip_and_rule_baseline(tmp_path) -> None:
    with _score_client(tmp_path) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
        completed = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        assert completed.json()["status"] == "completed"

        # 终态规则基线:outcome=1,source=rule
        rule = [
            item for item in _events(client, run_id) if item["type"] == "run.score.v1"
        ]
        assert len(rule) == 1
        assert rule[0]["payload"]["name"] == "outcome"
        assert rule[0]["payload"]["value"] == 1.0
        assert rule[0]["payload"]["source"] == "rule"

        # 人工评分:落事件并可从 GET /scores 回放
        resp = client.post(
            f"/api/v1/runs/{run_id}/scores",
            json={"value": 0, "comment": "结果不对"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 201
        created = resp.json()
        assert created["name"] == "user-feedback"
        assert created["value"] == 0.0
        assert created["source"] == "human"
        assert created["comment"] == "结果不对"

        listed = client.get(f"/api/v1/runs/{run_id}/scores", headers=ACCOUNT).json()
        assert [item["source"] for item in listed] == ["rule", "human"]
        assert listed[1]["value"] == 0.0

        # 账号隔离:别的账号看不到该 run 的评分
        assert (
            client.get(f"/api/v1/runs/{run_id}/scores", headers=OTHER).status_code
            == 404
        )
        # 数值越界由 schema 拦下
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/scores",
                json={"value": 2},
                headers=ACCOUNT,
            ).status_code
            == 422
        )


async def test_schedule_push_registers_after_commit(monkeypatch) -> None:
    sent: list[tuple[str, str, float, str | None]] = []

    async def fake_create_score(
        settings,
        *,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None = None,
        client=None,
    ) -> bool:
        sent.append((trace_id, name, value, comment))
        return True

    monkeypatch.setattr(langfuse, "create_score", fake_create_score)
    settings = SimpleNamespace(
        langfuse_enabled=True,
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key="sk-lf-test",
        langfuse_host="http://langfuse.test",
    )
    session = SimpleNamespace(info={})
    bind_runtime(session, PostCommitRuntime(session_factory=None, settings=settings))
    run = SimpleNamespace(id="run-1", trace_id=TRACE_ID)

    run_scores._schedule_push(
        session,
        run,
        run_scores.ScoreDraft(name="user-feedback", value=1.0),
    )

    callbacks = session.info["agent_orchestrator.post_commit_callbacks"]
    assert len(callbacks) == 1
    await drain_after_commit(session)
    assert sent == [(TRACE_ID, "user-feedback", 1.0, None)]


def test_schedule_push_skips_without_trace_id() -> None:
    session = SimpleNamespace(info={})
    run = SimpleNamespace(id="run-1", trace_id=None)
    run_scores._schedule_push(
        session,
        run,
        run_scores.ScoreDraft(name="user-feedback", value=1.0),
    )
    assert "agent_orchestrator.post_commit_callbacks" not in session.info


async def test_rule_score_skipped_without_langfuse_runtime() -> None:
    session = SimpleNamespace(info={})
    run = SimpleNamespace(id="run-1", trace_id=None)
    assert (await run_scores.record_rule_score(session, run, completed=True)) is None