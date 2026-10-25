"""步骤视图(S1):run.step.v1 事件投影 + GET /runs/{id}/steps(零迁移)。

契约见 docs/gap-2026-10/07-run-step-model.md:步骤是事件投影而非表;
字段含 step_id/node/phase/attempt/round/status/起止时间/耗时;
步骤记录不进用户事件回放与 SSE,统一走本端点。
"""

from datetime import datetime

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeLLM, _client


def _create(client: TestClient) -> str:
    body = {
        "title": "步骤",
        "goal": "整理本周日程",
        "acceptance_criteria": ["日程已整理"],
    }
    resp = client.post("/api/v1/runs", json=body, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()["id"]


def _steps(
    client: TestClient, run_id: str, headers: dict[str, str] = TENANT
) -> list[dict]:
    resp = client.get(f"/api/v1/runs/{run_id}/steps", headers=headers)
    assert resp.status_code == 200
    return resp.json()


def _event_types(client: TestClient, run_id: str) -> list[str]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return [event["type"] for event in resp.json()]


def _assert_shape(step: dict) -> None:
    """每个 step 的字段齐全且可定位(node/phase/attempt/round/起止/耗时)。"""
    assert step["step_id"]
    assert step["node"] in {"plan", "execute", "tool_exec", "verify", "deliver"}
    assert step["phase"] in {"plan", "execute", "verify", "deliver"}
    assert isinstance(step["attempt"], int)
    assert isinstance(step["round"], int) and step["round"] >= 1
    assert step["status"] in {"running", "completed", "failed", "blocked"}
    started = datetime.fromisoformat(step["started_at"])
    finished = datetime.fromisoformat(step["finished_at"])
    assert finished >= started
    assert isinstance(step["duration_ms"], int) and step["duration_ms"] >= 0


def test_steps_projection_full_lifecycle(client: TestClient) -> None:
    run_id = _create(client)
    # 尚未执行:空列表(不是 404)
    assert _steps(client, run_id) == []

    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    planned = _steps(client, run_id)
    assert [step["node"] for step in planned] == ["plan"]
    _assert_shape(planned[0])
    assert planned[0]["status"] == "completed"

    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    steps = _steps(client, run_id)
    assert [step["node"] for step in steps] == ["plan", "execute", "verify", "deliver"]
    assert [step["phase"] for step in steps] == [
        "plan",
        "execute",
        "verify",
        "deliver",
    ]
    for step in steps:
        _assert_shape(step)
        # 首次执行 attempt 从 1 起(模型默认),同一 attempt 内各节点 round=1
        assert step["attempt"] == 1
        assert step["round"] == 1
        assert step["status"] == "completed"
    # 可寻址:step_id 唯一;seq 单调(投影按 seq 排序)
    assert len({step["step_id"] for step in steps}) == len(steps)
    assert [step["seq"] for step in steps] == sorted(step["seq"] for step in steps)
    # 步骤记录是投影底层记录:不进用户事件回放
    assert "run.step.v1" not in _event_types(client, run_id)


def test_steps_survive_reject_and_retry_with_new_attempt(client: TestClient) -> None:
    run_id = _create(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(
        f"/api/v1/runs/{run_id}/reject", json={"reason": "再改改"}, headers=TENANT
    )
    resp = client.post(f"/api/v1/runs/{run_id}/resume", headers=TENANT)
    assert resp.status_code == 200
    resp = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert resp.json()["status"] == "completed"

    steps = _steps(client, run_id)
    # 返工/恢复会推高 attempt:两次调用边界各自采集,step_id 仍唯一可寻址
    assert len({step["step_id"] for step in steps}) == len(steps)
    assert {step["attempt"] for step in steps} == {1, 2}
    # resume 会重跑规划(attempt 2),再执行/验收/交付:同名节点靠 attempt 区分
    assert [step["node"] for step in steps] == [
        "plan",
        "plan",
        "execute",
        "verify",
        "deliver",
    ]
    assert [step["attempt"] for step in steps] == [1, 2, 2, 2, 2]
    assert all(step["round"] == 1 for step in steps)
    assert steps[-1]["node"] == "deliver"
    for step in steps:
        _assert_shape(step)


def test_failed_node_is_recorded_before_run_failed(tmp_path) -> None:
    """异常路径:失败 span 也落库,且排在 run.failed.v1 之前(排障要看得到失败步)。"""
    with _client(tmp_path, FakeLLM(["# 计划"], fail_on_call=1)) as client:
        run_id = _create(client)
        resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        assert resp.json()["status"] == "failed"

        steps = _steps(client, run_id)
        assert [step["node"] for step in steps] == ["plan"]
        assert steps[0]["status"] == "failed"
        assert steps[0]["finished_at"] is not None

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        types = [event["type"] for event in events]
        assert "run.failed.v1" in types
        assert "run.step.v1" not in types
        failed_seq = next(e["seq"] for e in events if e["type"] == "run.failed.v1")
        assert steps[0]["seq"] < failed_seq  # 步骤先于终态落库


def test_steps_are_tenant_isolated(client: TestClient) -> None:
    run_id = _create(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)

    resp = client.get(f"/api/v1/runs/{run_id}/steps", headers=OTHER)
    assert resp.status_code == 404
    assert resp.json()["code"] == "run.not_found"
    assert (
        client.get("/api/v1/runs/nonexistent/steps", headers=TENANT).status_code == 404
    )
