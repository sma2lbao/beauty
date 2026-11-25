"""报告下载与下载审计:成功/失败/匿名/内部密钥四条链路。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import AUTH, INTERNAL
from tests.test_evals import case, report, suite, upload


def _run(client: TestClient) -> dict[str, Any]:
    response = upload(
        client,
        {
            "external_id": "download-1",
            "report": report(suite("agent-core", [case("chat/a")])),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_download_report_and_record_success_audit(client: TestClient) -> None:
    run = _run(client)
    response = client.get(f"/v1/runs/{run['id']}/download", headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="eval-{run["id"]}.json"'
    )
    assert response.json()["suites"][0]["cases"][0]["id"] == "chat/a"

    audits = client.get("/v1/downloads", headers=INTERNAL).json()
    assert audits["total"] == 1
    item = audits["items"][0]
    assert item["run_id"] == run["id"]
    assert item["actor"] == "t-1"
    assert item["actor_kind"] == "account"
    assert item["status"] == "success"
    assert item["report_size"] == len(response.content)
    assert item["format"] == "json"

    filtered = client.get(
        "/v1/downloads", params={"run_id": run["id"]}, headers=INTERNAL
    ).json()
    assert filtered["total"] == 1
    empty = client.get(
        "/v1/downloads", params={"run_id": "does-not-exist"}, headers=INTERNAL
    ).json()
    assert empty["total"] == 0


def test_download_failure_is_audited(client: TestClient) -> None:
    response = client.get("/v1/runs/missing/download", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["code"] == "eval.run_not_found"

    audits = client.get("/v1/downloads", headers=INTERNAL).json()
    assert audits["total"] == 1
    item = audits["items"][0]
    assert item["run_id"] == "missing"
    assert item["status"] == "failed"
    assert item["error_code"] == "eval.run_not_found"
    assert item["report_size"] == 0


def test_anonymous_download_is_audited_without_actor(anon_client: TestClient) -> None:
    run = _run(anon_client)
    response = anon_client.get(f"/v1/runs/{run['id']}/download")
    assert response.status_code == 200, response.text

    audits = anon_client.get("/v1/downloads", headers=INTERNAL).json()
    assert audits["total"] == 1
    assert audits["items"][0]["actor"] is None
    assert audits["items"][0]["actor_kind"] == "anonymous"


def test_download_audit_requires_internal_secret(client: TestClient) -> None:
    denied = client.get("/v1/downloads")
    assert denied.status_code == 403
    wrong = client.get("/v1/downloads", headers={"X-Internal-Secret": "wrong"})
    assert wrong.status_code == 403
