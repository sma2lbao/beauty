"""交付物镜像到工作区(§7 归属方向):成功如实记账、失败不丢交付物。"""

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import (
    TENANT,
    FakeLLM,
    FakeWorkspace,
    _create_run,
    _settings,
    verifier,
)

VERIFY_PASS = '{"summary": "验收通过", "verdicts": {"邮件已汇总": true}}'
DELIVERY = "# 本周周报\n\n- 邮件已汇总\n"


def _complete(client: TestClient) -> str:
    run_id = _create_run(client)
    client.post(f"/api/v1/runs/{run_id}/plan", headers=TENANT)
    response = client.post(f"/api/v1/runs/{run_id}/approve", headers=TENANT)
    assert response.json()["status"] == "completed"
    return run_id


def _delivered(client: TestClient, run_id: str) -> dict:
    events = client.get(f"/api/v1/runs/{run_id}/events", headers=TENANT).json()
    return next(e["payload"] for e in events if e["type"] == "run.delivered.v1")


def test_artifact_is_mirrored_to_workspace(tmp_path: Path) -> None:
    workspace = FakeWorkspace()
    app = create_app(
        _settings(tmp_path, "mirror.db", llm_enabled=True),
        llm_client=FakeLLM(["# 计划", "执行", VERIFY_PASS, DELIVERY]),
        workspace_client=workspace,
        token_verifier=verifier(),
    )

    with TestClient(app) as client:
        run_id = _complete(client)
        payload = _delivered(client, run_id)

        # 镜像成功:事件里带上工作区交付物 id,内容是最终成品
        assert payload["workspace_artifact_id"] == "ws-art-1"
        assert "workspace_error" not in payload
        mirrored = workspace.created[0]
        assert mirrored["tenant_id"] == "t-1"
        assert mirrored["run_id"] == run_id
        assert mirrored["content"] == DELIVERY
        assert mirrored["title"] == payload["title"]

        # 读契约不变:orchestrator 自己的取件仍然可用
        artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert artifact["content"] == DELIVERY


def test_mirror_failure_keeps_artifact_and_records_reason(tmp_path: Path) -> None:
    """工作区不可用时:run 照常交付,但把失败原因如实写进事件。"""
    app = create_app(
        _settings(tmp_path, "mirror-fail.db", llm_enabled=True),
        llm_client=FakeLLM(["# 计划", "执行", VERIFY_PASS, DELIVERY]),
        workspace_client=FakeWorkspace(fail=True),
        token_verifier=verifier(),
    )

    with TestClient(app) as client:
        run_id = _complete(client)
        payload = _delivered(client, run_id)

        assert "workspace_artifact_id" not in payload
        assert "工作区镜像失败" in payload["workspace_error"]
        # 交付物本身没丢
        artifact = client.get(f"/api/v1/runs/{run_id}/artifact", headers=TENANT).json()
        assert artifact["content"] == DELIVERY
