"""checkpoint 列表端点(P1-C,见 docs/design/recovery.md §5)。

只读、按线程归属、最新在前;摘要字段来自状态投影,不落 audit_logs。
"""

from __future__ import annotations

from tests.conftest import ACCOUNT, OTHER, FakeLLM, _client, _create_run


def _plan_run(client) -> str:
    run_id = _create_run(client)
    resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=ACCOUNT)
    assert resp.status_code == 200
    return run_id


def test_lists_checkpoints_newest_first_with_summary(tmp_path) -> None:
    fake = FakeLLM(
        [
            "# 计划",
            "执行",
            '{"summary": "ok", "verdicts": {"日程已整理": true}}',
            "# 交付物",
        ]
    )
    with _client(tmp_path, fake) as client:
        run_id = _plan_run(client)
        resp = client.get(f"/api/v1/runs/{run_id}/checkpoints", headers=ACCOUNT)
        assert resp.status_code == 200
        rows = resp.json()

        # 计划闸前的三次快照:审批闸(step1) → 计划(step0) → 输入(step-1),最新在前
        assert [row["step"] for row in rows] == [1, 0, -1]
        latest, planned, seeded = rows

        assert latest["checkpoint_id"]
        assert latest["parent_checkpoint_id"] == planned["checkpoint_id"]
        assert latest["source"] == "loop"
        assert latest["next"] == ["approval_gate"]
        assert latest["runnable"] is True
        assert latest["created_at"]

        assert planned["next"] == ["plan"]
        assert seeded["next"] == ["__start__"]

        summary = latest["summary"]
        assert set(summary) == {
            "rounds",
            "plan_chars",
            "tool_calls",
            "todos_count",
            "artifact_ready",
        }
        assert summary["plan_chars"] == len("# 计划")
        assert summary["artifact_ready"] is False
        # 输入快照还没有计划正文
        assert seeded["summary"]["plan_chars"] == 0


def test_checkpoints_limit_and_validation(tmp_path) -> None:
    fake = FakeLLM(["# 计划", "执行", "{}", "# 交付物"])
    with _client(tmp_path, fake) as client:
        run_id = _plan_run(client)
        url = f"/api/v1/runs/{run_id}/checkpoints"

        only_one = client.get(url, params={"limit": 1}, headers=ACCOUNT)
        assert only_one.status_code == 200
        assert len(only_one.json()) == 1

        assert client.get(url, params={"limit": 0}, headers=ACCOUNT).status_code == 422
        assert (
            client.get(url, params={"limit": 201}, headers=ACCOUNT).status_code == 422
        )


def test_checkpoints_are_account_scoped(tmp_path) -> None:
    fake = FakeLLM(["# 计划", "执行", "{}", "# 交付物"])
    with _client(tmp_path, fake) as client:
        run_id = _plan_run(client)
        resp = client.get(f"/api/v1/runs/{run_id}/checkpoints", headers=OTHER)
        assert resp.status_code == 404
        assert resp.json()["code"] == "run.not_found"
