"""rewind / fork(P1-C,见 docs/design/recovery.md §6/§7)。

rewind:终态 run 回退到某个 checkpoint 重跑,attempts 不变、走同一线程;
fork:以父 run 为模板开新 run,可选把某 checkpoint 的状态种入子线程。
"""

from __future__ import annotations

import asyncio

from database.session import create_session_factory, session_scope
from fastapi.testclient import TestClient
from sqlalchemy import select

from agent_orchestrator.main import create_app
from agent_orchestrator.models.runs import Job
from agent_orchestrator.services import runs as runs_service
from tests.conftest import (
    _IDP,
    ACCOUNT,
    FakeLLM,
    _client,
    _create_run,
    _settings,
)

VERIFY_PASS = '{"summary": "ok", "verdicts": {"日程已整理": true}}'


def _four_replies() -> list[str]:
    return ["# 计划", "执行", VERIFY_PASS, "# 交付物"]


def _complete_run(client) -> str:
    run_id = _create_run(client)
    assert (
        client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT).status_code == 200
    )
    approved = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
    assert approved.json()["status"] == "completed"
    return run_id


def _checkpoints(client, run_id: str) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/checkpoints", headers=ACCOUNT)
    assert resp.status_code == 200
    return resp.json()


def _plan_checkpoint(client, run_id: str) -> dict:
    """计划节点之后的快照:审批闸前的 checkpoint(带计划正文)。"""
    return next(
        row
        for row in _checkpoints(client, run_id)
        if row["next"] == ["approval_gate"] and row["summary"]["plan_chars"] > 0
    )


def test_rewind_completed_run_to_plan_checkpoint(tmp_path) -> None:
    # 前 4 次完成首轮;rewind 后只重跑 approval_gate 之后的 execute/verify/deliver
    fake = FakeLLM(_four_replies() + ["重跑执行", VERIFY_PASS, "# 重跑交付"])
    with _client(tmp_path, fake) as client:
        run_id = _complete_run(client)
        checkpoint = _plan_checkpoint(client, run_id)

        resp = client.post(
            f"/api/v1/runs/{run_id}/rewind",
            json={"checkpoint_id": checkpoint["checkpoint_id"], "reason": "重跑"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 200
        run = resp.json()
        # 回到审批闸:计划正文保留,等人工再次放行
        assert run["status"] == "awaiting_approval"
        assert run["attempts"] == 1
        plan = client.get(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT).json()
        assert plan["plan_markdown"] == "# 计划"

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=ACCOUNT).json()
        rewound = next(e for e in events if e["type"] == "run.rewind.v1")
        assert rewound["payload"] == {
            "checkpoint_id": checkpoint["checkpoint_id"],
            "from_status": "completed",
            "reason": "重跑",
        }

        # 再放行 → 只补跑后续节点,计划节点不再调用模型
        again = client.post(f"/api/v1/runs/{run_id}/approve", headers=ACCOUNT)
        assert again.json()["status"] == "completed"
        assert len(fake.calls) == 7


def test_rewind_rejects_unknown_checkpoint(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    with _client(tmp_path, fake) as client:
        run_id = _complete_run(client)
        resp = client.post(
            f"/api/v1/runs/{run_id}/rewind",
            json={"checkpoint_id": "not-a-real-checkpoint"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.checkpoint_not_found"
        # 失败不改变状态
        run = client.get(f"/api/v1/runs/{run_id}", headers=ACCOUNT).json()
        assert run["status"] == "completed"


def test_rewind_requires_terminal_state(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)  # pending,尚未执行
        resp = client.post(
            f"/api/v1/runs/{run_id}/rewind",
            json={"checkpoint_id": "whatever"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "run.invalid_state"


def test_fork_inherits_and_emits_event(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    with _client(tmp_path, fake) as client:
        parent_id = _complete_run(client)
        resp = client.post(f"/api/v1/runs/{parent_id}/fork", json={}, headers=ACCOUNT)
        assert resp.status_code == 201
        child = resp.json()

        assert child["id"] != parent_id
        assert child["status"] == "pending"
        assert child["parent_run_id"] == parent_id
        assert child["parent_kind"] == "fork"
        assert child["title"] == "周报（分叉）"
        assert child["goal"] == "整理本周日程"
        assert child["acceptance_criteria"] == ["日程已整理"]
        assert child["priority"] == 0
        assert child["timeout_seconds"] is None

        events = client.get(
            f"/api/v1/runs/{child['id']}/events", headers=ACCOUNT
        ).json()
        created = next(e for e in events if e["type"] == "run.created.v1")
        assert created["payload"]["forked_from"] == parent_id
        assert created["payload"]["parent_kind"] == "fork"
        assert "checkpoint_id" not in created["payload"]

        # 子 run 全新线程:没有历史;父 run 不受影响
        assert _checkpoints(client, child["id"]) == []
        parent = client.get(f"/api/v1/runs/{parent_id}", headers=ACCOUNT).json()
        assert parent["status"] == "completed"


def test_fork_with_checkpoint_seeds_child(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    with _client(tmp_path, fake) as client:
        parent_id = _complete_run(client)
        checkpoint = _plan_checkpoint(client, parent_id)
        resp = client.post(
            f"/api/v1/runs/{parent_id}/fork",
            json={"checkpoint_id": checkpoint["checkpoint_id"], "title": "分支"},
            headers=ACCOUNT,
        )
        assert resp.status_code == 201
        child = resp.json()
        assert child["title"] == "分支"

        events = client.get(
            f"/api/v1/runs/{child['id']}/events", headers=ACCOUNT
        ).json()
        created = next(e for e in events if e["type"] == "run.created.v1")
        assert created["payload"]["checkpoint_id"] == checkpoint["checkpoint_id"]

        # 父状态种入子线程:子 run 有 1 条快照,停在审批闸之前
        rows = _checkpoints(client, child["id"])
        assert len(rows) == 1
        assert rows[0]["next"] == ["approval_gate"]
        assert rows[0]["summary"]["plan_chars"] == len("# 计划")


def test_rewind_with_jobs_enqueues_rewind_job(tmp_path) -> None:
    fake = FakeLLM(_four_replies())
    settings = _settings(tmp_path, "jobs.db", llm_enabled=True)
    app = create_app(settings, llm_client=fake, token_verifier=_IDP.verifier())
    with TestClient(app) as client:
        run_id = _complete_run(client)
        checkpoint_id = _plan_checkpoint(client, run_id)["checkpoint_id"]

    # 同步 API 路径不跑 worker:直接调服务走 jobs 分支,断言入队而不执行
    async def scenario() -> tuple[str, dict, str, str]:
        factory = create_session_factory(settings.database_url)
        try:
            async with session_scope(factory) as session:
                run = await runs_service.rewind_run(
                    session,
                    app.state.container.run_graph,
                    "t-1",
                    run_id,
                    checkpoint_id,
                    jobs_enabled=True,
                )
                job = await session.scalar(select(Job).where(Job.run_id == run.id))
                assert job is not None
                return run.status, dict(job.payload), job.kind, job.status
        finally:
            await factory.engine.dispose()

    status, payload, kind, job_status = asyncio.run(scenario())
    assert status == "running"
    assert payload == {"checkpoint_id": checkpoint_id}
    assert (kind, job_status) == ("rewind", "queued")
