"""failed 的重试入口(§6.2):另起一跑并继承上下文,不重放旧图、不篡改旧历史。"""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeLLM, _client, _create_run

VERIFY_PASS = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'


def _make_failed_run(client: TestClient) -> str:
    """造一个 failed run:计划成功,execute 阶段模型挂掉。"""
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
    assert run["status"] == "failed"
    return run_id


def _failing_llm() -> FakeLLM:
    return FakeLLM(["# 计划:汇总邮件", "执行"], fail_on_call=2)


def test_retry_creates_child_run_with_inherited_context(tmp_path: Path) -> None:
    with _client(tmp_path, _failing_llm()) as client:
        parent_id = _make_failed_run(client)
        parent = client.get(f"/api/v1/runs/{parent_id}", headers=TENANT).json()

        response = client.post(f"/api/v1/runs/{parent_id}/retry", headers=TENANT)
        assert response.status_code == 201
        child = response.json()

        # 新 run:血缘 + 标题标记;目标/验收标准/计划继承;轮次与尝试重新开始
        assert child["id"] != parent_id
        assert child["parent_run_id"] == parent_id
        assert child["title"].endswith("(重试)")
        assert child["goal"] == parent["goal"]
        assert child["acceptance_criteria"] == parent["acceptance_criteria"]
        assert child["attempts"] == 1
        assert child["rounds"] == 0
        # 计划节点检测到已有计划会短路 → 直接进审批闸,不重复调用模型
        assert child["status"] == "awaiting_approval"
        assert child["status_reason"] is None

        plan = client.get(f"/api/v1/runs/{child['id']}/plan", headers=TENANT).json()
        parent_plan = client.get(
            f"/api/v1/runs/{parent_id}/plan", headers=TENANT
        ).json()
        assert plan["plan_markdown"] == parent_plan["plan_markdown"]


def test_retry_keeps_parent_history_intact(tmp_path: Path) -> None:
    with _client(tmp_path, _failing_llm()) as client:
        parent_id = _make_failed_run(client)
        child = client.post(f"/api/v1/runs/{parent_id}/retry", headers=TENANT).json()

        parent = client.get(f"/api/v1/runs/{parent_id}", headers=TENANT).json()
        assert parent["status"] == "failed"  # 旧 run 仍是 failed,不被"重试"改写
        assert parent["status_reason"]  # 失败原因保留
        runs = client.get("/api/v1/runs", headers=TENANT).json()
        assert {run["id"] for run in runs} == {parent_id, child["id"]}


def test_retry_events_link_both_directions(tmp_path: Path) -> None:
    with _client(tmp_path, _failing_llm()) as client:
        parent_id = _make_failed_run(client)
        child = client.post(f"/api/v1/runs/{parent_id}/retry", headers=TENANT).json()

        parent_events = client.get(
            f"/api/v1/runs/{parent_id}/events", headers=TENANT
        ).json()
        requested = next(
            e for e in parent_events if e["type"] == "run.retry.requested.v1"
        )
        assert requested["payload"]["new_run_id"] == child["id"]
        assert requested["payload"]["reason"]

        child_events = client.get(
            f"/api/v1/runs/{child['id']}/events", headers=TENANT
        ).json()
        created = next(e for e in child_events if e["type"] == "run.created.v1")
        assert created["payload"]["parent_run_id"] == parent_id


def test_retry_only_allowed_from_failed(tmp_path: Path) -> None:
    """只有 failed 能重试;其它状态 409(blocked 走 resume,completed 无需重试)。"""
    fake = FakeLLM(["# 计划", "执行", VERIFY_PASS, "# 成品"])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)

        early = client.post(f"/api/v1/runs/{run_id}/retry", headers=TENANT)
        assert early.status_code == 409
        assert early.json()["code"] == "run.invalid_state"

        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
        late = client.post(f"/api/v1/runs/{run_id}/retry", headers=TENANT)
        assert late.status_code == 409


def test_retry_is_tenant_scoped(tmp_path: Path) -> None:
    with _client(tmp_path, _failing_llm()) as client:
        parent_id = _make_failed_run(client)
        response = client.post(f"/api/v1/runs/{parent_id}/retry", headers=OTHER)
        assert response.status_code == 404
        assert response.json()["code"] == "run.not_found"


def test_retried_run_can_complete(tmp_path: Path) -> None:
    """重试后沿继承的计划走完:审批 → 执行 → 验收 → 交付。"""
    fake = _failing_llm()
    with _client(tmp_path, fake) as client:
        parent_id = _make_failed_run(client)
        child = client.post(f"/api/v1/runs/{parent_id}/retry", headers=TENANT).json()

        # 第 2 次调用(失败的 execute)已消耗 responses[1];后续按序继续
        fake.responses.extend(["执行成功", VERIFY_PASS, "# 重试后的成品"])

        approved = client.post(
            f"/api/v1/runs/{child['id']}/approve", headers=TENANT
        ).json()
        assert approved["status"] == "completed"

        artifact = client.get(
            f"/api/v1/runs/{child['id']}/artifact", headers=TENANT
        ).json()
        assert "重试后的成品" in artifact["content"]
