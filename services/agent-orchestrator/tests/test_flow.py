"""run 生命周期全流程:plan → edit → approve / reject → blocked → resume。"""

from fastapi.testclient import TestClient

from tests.conftest import TENANT


def _create(client: TestClient) -> dict:
    body = {
        "title": "周报",
        "goal": "整理本周日程生成周报",
        "acceptance_criteria": ["覆盖 5 个工作日", "不超过 300 字"],
    }
    resp = client.post("/api/v1/runs", json=body, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()


def _event_types(client: TestClient, run_id: str) -> list[str]:
    resp = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT)
    assert resp.status_code == 200
    return [e["type"] for e in resp.json()]


def test_plan_then_approve_full_lifecycle(client: TestClient) -> None:
    run_id = _create(client)["id"]

    resp = client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    assert resp.status_code == 200
    planned = resp.json()
    assert planned["status"] == "awaiting_approval"
    assert "整理本周日程生成周报" in planned["plan_markdown"]
    assert "- [ ] 覆盖 5 个工作日" in planned["plan_markdown"]
    assert _event_types(client, run_id) == ["run.created.v1", "run.planned.v1"]

    # 审批前编辑计划
    edited = "# 我的计划\n\n按新计划执行"
    resp = client.patch(
        f"/api/v1/runs/{run_id}/plan", json={"plan_markdown": edited}, headers=TENANT
    )
    assert resp.status_code == 200
    assert resp.json()["plan_markdown"] == edited

    resp = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert resp.status_code == 200
    done = resp.json()
    assert done["status"] == "completed"
    assert done["rounds"] >= 1
    assert done["attempts"] == 1

    types = _event_types(client, run_id)
    assert "run.plan.edited.v1" in types
    assert "run.approved.v1" in types
    assert "run.completed.v1" in types

    # 完成后不允许再动
    resp = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert resp.status_code == 409
    assert resp.json()["code"] == "run.invalid_state"


def test_reject_blocks_then_resume_allows_new_approval(client: TestClient) -> None:
    run_id = _create(client)["id"]
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)

    edited = "# 修订后的计划"
    client.patch(
        f"/api/v1/runs/{run_id}/plan", json={"plan_markdown": edited}, headers=TENANT
    )

    resp = client.post(
        f"/api/v1/runs/{run_id}/reject", json={"reason": "方向不对"}, headers=TENANT
    )
    assert resp.status_code == 200
    blocked = resp.json()
    assert blocked["status"] == "blocked"
    assert blocked["status_reason"] == "方向不对"
    types = _event_types(client, run_id)
    assert "run.rejected.v1" in types
    assert "run.blocked.v1" in types

    # blocked 状态下 approve 被状态守卫拦下
    assert (
        client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).status_code == 409
    )

    # resume:保留已编辑计划,重新进审批闸
    resp = client.post(f"/api/v1/runs/{run_id}/resume", headers=TENANT)
    assert resp.status_code == 200
    resumed = resp.json()
    assert resumed["status"] == "awaiting_approval"
    assert resumed["plan_markdown"] == edited  # 计划未被覆盖
    assert "run.resumed.v1" in _event_types(client, run_id)

    resp = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert resp.status_code == 200
    done = resp.json()
    assert done["status"] == "completed"
    assert done["attempts"] == 2


def test_state_guards(client: TestClient) -> None:
    run_id = _create(client)["id"]
    # pending 阶段不允许 approve / 编辑计划
    assert (
        client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).status_code == 409
    )
    resp = client.patch(
        f"/api/v1/runs/{run_id}/plan",
        json={"plan_markdown": "# 早了"},
        headers=TENANT,
    )
    assert resp.status_code == 409
    # 重复 plan 也算状态冲突
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    assert client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT).status_code == 409


def test_not_found_uses_unified_error_model(client: TestClient) -> None:
    resp = client.get("/api/v1/runs/nonexistent/plan", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "run.not_found"
