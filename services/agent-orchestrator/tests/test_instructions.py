"""追加指令(§6.5):排队 → 边界消费(规划/放行)→ 并入提示词;终态拒绝。"""

from fastapi.testclient import TestClient

from tests.conftest import TENANT, FakeLLM, _client, _create_run


def test_queue_list_events_and_terminal_guard(client: TestClient) -> None:
    run_id = _create_run(client)
    resp = client.post(
        f"/api/v1/runs/{run_id}/instructions",
        json={"content": "顺便把日历也汇总一下"},
        headers=TENANT,
    )
    assert resp.status_code == 201
    queued = resp.json()
    assert queued["status"] == "queued"
    assert queued["run_id"] == run_id

    listed = client.get(f"/api/v1/runs/{run_id}/instructions", headers=TENANT).json()
    assert [item["content"] for item in listed] == ["顺便把日历也汇总一下"]

    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    assert "run.instruction.queued.v1" in [event["type"] for event in events]

    # 未完成的 run 可以继续排队;终态 run 追加 → 409
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    rejected = client.post(
        f"/api/v1/runs/{run_id}/instructions",
        json={"content": "再来一条"},
        headers=TENANT,
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "run.invalid_state"


def test_instruction_tenant_isolation(client: TestClient) -> None:
    run_id = _create_run(client)
    other = {"X-Tenant-Id": "t-2"}
    assert (
        client.get(f"/api/v1/runs/{run_id}/instructions", headers=other).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/runs/{run_id}/instructions",
            json={"content": "偷看别人的 run"},
            headers=other,
        ).status_code
        == 404
    )


def test_instruction_consumed_at_plan_boundary(tmp_path) -> None:
    """排队 → 规划边界消费:提示词带上指令、状态转 consumed、事件成对。"""
    fake = FakeLLM(["# 计划"])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(
            f"/api/v1/runs/{run_id}/instructions",
            json={"content": "顺便汇总日历"},
            headers=TENANT,
        )
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)

        plan_prompt = fake.calls[0][1]["content"]
        assert "用户追加指令" in plan_prompt
        assert "顺便汇总日历" in plan_prompt

        listed = client.get(
            f"/api/v1/runs/{run_id}/instructions", headers=TENANT
        ).json()
        assert listed[0]["status"] == "consumed"

        types = [
            event["type"]
            for event in client.get(
                f"/api/v1/runs/{run_id}/events", headers=TENANT
            ).json()
        ]
        assert "run.instruction.consumed.v1" in types
        assert types.index("run.instruction.queued.v1") < types.index(
            "run.instruction.consumed.v1"
        )


def test_instruction_consumed_at_approve_boundary(tmp_path) -> None:
    """审批前排队 → 放行时消费:execute 提示词带上指令,且只消费一次。"""
    verify_pass = '{"summary": "ok", "verdicts": {"邮件已汇总": true}}'
    fake = FakeLLM(["# 计划", "执行结果", verify_pass, "# 交付物"])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        client.post(
            f"/api/v1/runs/{run_id}/instructions",
            json={"content": "也看看日历"},
            headers=TENANT,
        )
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        execute_prompt = fake.calls[1][1]["content"]
        assert "也看看日历" in execute_prompt

        consumed = [
            event
            for event in client.get(
                f"/api/v1/runs/{run_id}/events", headers=TENANT
            ).json()
            if event["type"] == "run.instruction.consumed.v1"
        ]
        assert len(consumed) == 1
