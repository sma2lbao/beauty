"""交付物(§7):LLM 合成落库、桩拼装、取件、租户隔离与未交付 404。"""

from fastapi.testclient import TestClient

from tests.conftest import OTHER, TENANT, FakeLLM, _client, _create_run


def test_llm_delivery_persisted_with_event(tmp_path) -> None:
    verify_pass = '{"summary": "验收通过", "verdicts": {"日程已整理": true}}'
    delivery = "# 本周周报\n\n- 日程已整理\n"
    fake = FakeLLM(["# 计划", "执行结果:日程已整理", verify_pass, delivery])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "completed"

        artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert artifact["content"] == delivery
        assert artifact["kind"] == "markdown"
        assert artifact["title"] == "周报"
        assert artifact["run_id"] == run_id

        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        assert [e["type"] for e in events] == [
            "run.created.v1",
            "run.planned.v1",
            "run.approved.v1",
            "run.delivered.v1",
            "run.completed.v1",
        ]
        delivered = next(e for e in events if e["type"] == "run.delivered.v1")
        assert delivered["payload"]["chars"] == len(delivery)
        assert delivered["payload"]["title"] == "周报"


def test_stub_delivery_assembles_artifact(client: TestClient) -> None:
    """无 LLM(桩路径):deliver 拼装交付物并标注桩来源,同样可取件。"""
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    status_code = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
    assert status_code["status"] == "completed"

    artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
    assert artifact["title"] == "周报"
    assert "# 周报" in artifact["content"]
    assert "## 执行结果" in artifact["content"]
    assert "阶段 A 桩" in artifact["content"]


def test_artifact_missing_and_tenant_isolation(client: TestClient) -> None:
    """未交付(仅创建)→ 404 artifact.not_found;跨租户取件不可见。"""
    run_id = _create_run(client)
    resp = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT)
    assert resp.status_code == 404
    assert resp.json()["code"] == "artifact.not_found"

    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    other = OTHER
    assert (
        client.get(f"/api/v1/runs/{run_id}/artifact", headers=other).status_code == 404
    )


def test_failed_run_has_no_artifact(tmp_path) -> None:
    """failed 终态不产交付物:取件 404,事件流也无 delivered(诚实语义)。"""
    fail_json = '{"summary": "仍未达标", "verdicts": {"日程已整理": false}}'
    fake = FakeLLM(["# 计划", "执行", fail_json, "执行", fail_json, "执行", fail_json])
    with _client(tmp_path, fake) as client:
        run_id = _create_run(client)
        client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
        run = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT).json()
        assert run["status"] == "failed"
        assert (
            client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).status_code
            == 404
        )
        events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
        assert "run.delivered.v1" not in [e["type"] for e in events]


def test_artifact_not_found_details_uses_run_id(client: TestClient) -> None:
    """未交付时取件的 404 details 用 run_id(入口是 run;由仓储声明)。"""
    run_id = _create_run(client)
    response = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "artifact.not_found"
    assert body["details"] == {"run_id": run_id}


def test_run_not_found_details_key(client: TestClient) -> None:
    """run 404 的 details 键名是 run_id(由仓储 id_detail_key 声明,别改名)。"""
    response = client.get("/api/v1/runs/nope", headers=TENANT)
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "run.not_found"
    assert body["details"] == {"run_id": "nope"}
