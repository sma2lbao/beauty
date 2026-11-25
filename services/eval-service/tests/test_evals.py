"""eval-service:上报/索引/详情/分页/基线全链路(进程内 sqlite,零网络)。"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from eval_service.main import create_app
from tests.conftest import AUTH, INTERNAL, _settings


def case(
    case_id: str,
    *,
    ok: bool = True,
    score: float = 1.0,
    tokens: int = 10,
    duration_ms: float = 5.0,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "group": case_id.split("/")[0],
        "tags": ["offline"],
        "ok": ok,
        "score": score,
        "duration_ms": duration_ms,
        "tokens": tokens,
        "output": f"输出 {case_id}",
        "error": error,
        "fields": {"reply_0": "你好"},
        "events": ["run.created.v1"],
        "tool_calls": ["search_knowledge"],
        "metrics": {"recall_at_k": 1.0},
        "graders": [
            {
                "id": "reply",
                "type": "contains",
                "ok": ok,
                "required": True,
                "weight": 1.0,
                "message": "" if ok else "回复缺少关键词",
            }
        ],
        "failures": [] if ok else ["回复缺少关键词"],
    }


def suite(name: str, cases: list[dict[str, Any]], version: int = 1) -> dict[str, Any]:
    passed = sum(1 for item in cases if item["ok"])
    return {
        "name": name,
        "dataset": f"evals/datasets/{name}",
        "dataset_version": version,
        "generated_at": "2026-11-25T00:00:00+00:00",
        "totals": {
            "cases": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
            "pass_rate": passed / len(cases) if cases else 0.0,
            "score": 1.0,
            "tokens": 0,
            "duration_ms": 0.0,
        },
        "cases": cases,
    }


def report(*suites: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": "2026-11-25T00:00:00+00:00",
        "totals": {},
        "suites": list(suites),
    }


def upload(
    client: TestClient,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/v1/runs",
        json=body,
        headers=INTERNAL if headers is None else headers,
    )


def test_upload_index_and_detail(client: TestClient) -> None:
    response = upload(
        client,
        {
            "external_id": "run-1",
            "trigger": "ci",
            "git_sha": "a" * 40,
            "branch": "main",
            "model": "scripted",
            "environment": "ci",
            "report": report(
                suite("agent-core", [case("chat/a"), case("chat/b")]),
                suite("retrieval", [case("q01")]),
            ),
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["totals"]["cases"] == 3
    assert run["totals"]["failed"] == 0
    assert run["dataset_versions"] == {"agent-core": 1, "retrieval": 1}

    index = client.get("/v1/runs", headers=AUTH).json()
    assert index["runs"][0]["id"] == run["id"]
    assert index["runs"][0]["suites"][0]["name"] == "agent-core"

    detail = client.get(f"/v1/runs/{run['id']}", headers=AUTH).json()
    assert detail["suites"][0]["cases"][0]["fields"]["reply_0"] == "你好"
    assert detail["drifts"] == []
    assert detail["suites"][0]["drift_count"] == 0


def test_upload_is_idempotent_by_external_id(client: TestClient) -> None:
    body = {
        "external_id": "same",
        "report": report(suite("agent-core", [case("chat/a")])),
    }
    first = upload(client, body).json()
    second = upload(client, body)
    assert second.status_code == 201
    assert second.json()["id"] == first["id"]
    assert len(client.get("/v1/runs", headers=AUTH).json()["runs"]) == 1


def test_rejects_bad_report_and_bad_secret(client: TestClient) -> None:
    bad = upload(client, {"report": {"version": 1, "suites": []}})
    assert bad.status_code == 400
    assert bad.json()["code"] == "eval.report_invalid"

    unauthorized = upload(
        client,
        {"report": report(suite("agent-core", [case("chat/a")]))},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert unauthorized.status_code == 403

    anonymous = upload(
        client,
        {"report": report(suite("agent-core", [case("chat/a")]))},
        headers={},
    )
    assert anonymous.status_code == 403


def test_ingest_fails_closed_without_secret(tmp_path) -> None:
    app = create_app(_settings(tmp_path, internal_secret=""))
    from fastapi.testclient import TestClient

    with TestClient(app) as local:
        response = upload(
            local,
            {"report": report(suite("agent-core", [case("chat/a")]))},
        )
    assert response.status_code == 503
    assert response.json()["code"] == "eval.ingest_disabled"


def test_drift_is_annotated_from_uploaded_baseline(client: TestClient) -> None:
    current = case("chat/a", ok=False, score=0.0, error="boom")
    baseline = {
        "version": 1,
        "suites": [
            {
                "name": "agent-core",
                "dataset": "evals/datasets/agent-core",
                "dataset_version": 1,
                "cases": {
                    "chat/a": {
                        "ok": True,
                        "score": 1.0,
                        "tokens": 10,
                        "metrics": {},
                        "events": ["run.created.v1"],
                        "tool_calls": ["search_knowledge"],
                    }
                },
            }
        ],
    }
    response = upload(
        client,
        {
            "external_id": "drift-1",
            "report": report(suite("agent-core", [current])),
            "baselines": {"agent-core": baseline},
        },
    )
    run = response.json()
    assert run["drift_count"] >= 1
    assert run["totals"]["failed"] == 1
    assert run["failed_cases"][0]["case_id"] == "chat/a"

    detail = client.get(f"/v1/runs/{run['id']}", headers=AUTH).json()
    assert detail["suites"][0]["drift_count"] >= 1
    assert detail["suites"][0]["cases"][0]["baseline_diff"]
    kinds = {item["kind"] for item in detail["drifts"]}
    assert "failed" in kinds


def test_approved_baseline_is_used_and_listed(client: TestClient) -> None:
    payload = {
        "version": 1,
        "suites": [
            {
                "name": "agent-core",
                "dataset": "evals/datasets/agent-core",
                "dataset_version": 1,
                "cases": {
                    "chat/a": {
                        "ok": True,
                        "score": 1.0,
                        "tokens": 10,
                        "metrics": {},
                        "events": [],
                        "tool_calls": [],
                    }
                },
            }
        ],
    }
    approved = client.post(
        "/v1/baselines/agent-core",
        json={
            "payload": payload,
            "dataset_version": 1,
            "evaluator_version": "1",
            "git_sha": "b" * 40,
        },
        headers=AUTH,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == "t-1"
    assert approved.json()["case_count"] == 1
    assert approved.json()["passed"] == 1

    listed = client.get("/v1/baselines", headers=AUTH).json()["items"]
    assert listed[0]["suite"] == "agent-core"
    assert listed[0]["case_count"] == 1
    assert client.get("/v1/baselines/agent-core", headers=AUTH).json() == payload

    # 上报不带 baselines 时回落到已批准基线,失败用例仍能算出漂移。
    response = upload(
        client,
        {
            "external_id": "approved-1",
            "report": report(
                suite("agent-core", [case("chat/a", ok=False, score=0.0)])
            ),
        },
    )
    assert response.json()["drift_count"] >= 1

    missing = client.get("/v1/baselines/retrieval", headers=AUTH)
    assert missing.status_code == 400
    assert missing.json()["code"] == "eval.baseline_not_found"


def test_case_pagination_and_filters(client: TestClient) -> None:
    cases = [
        case("chat/a"),
        case("chat/b", ok=False, score=0.0),
        case("chat/c", ok=False, score=0.0),
    ]
    body = {
        "external_id": "page-1",
        "report": report(suite("agent-core", cases)),
    }
    run = upload(client, body).json()
    run_id = run["id"]

    page = client.get(
        f"/v1/runs/{run_id}/suites/agent-core/cases",
        params={"status": "failed", "limit": 1, "offset": 0},
        headers=AUTH,
    ).json()
    assert page["total"] == 2
    assert len(page["items"]) == 1
    assert page["items"][0]["ok"] is False

    second = client.get(
        f"/v1/runs/{run_id}/suites/agent-core/cases",
        params={"status": "failed", "limit": 1, "offset": 1},
        headers=AUTH,
    ).json()
    assert second["items"][0]["id"] != page["items"][0]["id"]

    missing = client.get(f"/v1/runs/{run_id}/suites/unknown/cases", headers=AUTH)
    assert missing.status_code == 400


def test_run_filters_and_not_found(client: TestClient) -> None:
    upload(
        client,
        {
            "external_id": "filter-ok",
            "branch": "main",
            "report": report(suite("agent-core", [case("chat/a")])),
        },
    )
    upload(
        client,
        {
            "external_id": "filter-fail",
            "branch": "dev",
            "report": report(suite("agent-core", [case("chat/b", ok=False)])),
        },
    )
    failed = client.get("/v1/runs", params={"status": "failed"}, headers=AUTH).json()[
        "runs"
    ]
    assert [item["id"] for item in failed] == [
        client.get("/v1/runs", headers=AUTH).json()["runs"][0]["id"]
    ]
    dev = client.get("/v1/runs", params={"branch": "dev"}, headers=AUTH).json()
    assert dev["runs"][0]["branch"] == "dev"
    by_suite = client.get(
        "/v1/runs", params={"suite": "agent-core"}, headers=AUTH
    ).json()["runs"]
    assert len(by_suite) == 2

    missing = client.get("/v1/runs/does-not-exist", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["code"] == "eval.run_not_found"


def test_compare_runs_marks_regressions_and_fixes(client: TestClient) -> None:
    base = upload(
        client,
        {
            "external_id": "cmp-base",
            "report": report(
                suite(
                    "agent-core",
                    [case("chat/a"), case("chat/b", ok=False), case("chat/c")],
                ),
                suite("retrieval", [case("q01")]),
            ),
        },
    ).json()
    candidate = upload(
        client,
        {
            "external_id": "cmp-candidate",
            "report": report(
                suite(
                    "agent-core",
                    [
                        case("chat/a", ok=False, score=0.0),
                        case("chat/b", tokens=30),
                        case("chat/d"),
                    ],
                )
            ),
        },
    ).json()

    response = client.get(
        "/v1/compare",
        params={"base": base["id"], "candidate": candidate["id"]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["base"]["id"] == base["id"]
    assert body["candidate"]["id"] == candidate["id"]
    assert body["totals"] == {
        "regressed": 1,
        "fixed": 1,
        "added": 1,
        "removed": 2,
        "unchanged": 0,
        "base_pass_rate": 0.75,
        "candidate_pass_rate": 0.6667,
        "pass_rate_delta": -0.0833,
        "tokens_delta": 10,
        "duration_delta_ms": -5.0,
    }
    assert [item["name"] for item in body["suites"]] == ["agent-core", "retrieval"]
    assert body["suites"][1]["candidate_totals"] is None
    statuses = {
        (item["suite"], item["case_id"]): item["status"] for item in body["cases"]
    }
    assert statuses[("agent-core", "chat/a")] == "regressed"
    assert statuses[("agent-core", "chat/b")] == "fixed"
    assert statuses[("agent-core", "chat/c")] == "removed"
    assert statuses[("agent-core", "chat/d")] == "added"
    assert statuses[("retrieval", "q01")] == "removed"

    missing = client.get(
        "/v1/compare",
        params={"base": "nope", "candidate": candidate["id"]},
        headers=AUTH,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "eval.run_not_found"


def test_reads_allow_anonymous_when_auth_disabled(anon_client: TestClient) -> None:
    """AUTH_ENABLED=false 时只读端点不要求账号头(否则页面只能回退静态快照)。"""

    assert anon_client.app.state.token_verifier is None
    run = upload(
        anon_client,
        {
            "external_id": "anon-read",
            "report": report(suite("agent-core", [case("chat/a")])),
        },
    ).json()
    index = anon_client.get("/v1/runs")
    assert index.status_code == 200
    assert index.json()["runs"][0]["id"] == run["id"]
    assert anon_client.get(f"/v1/runs/{run['id']}").status_code == 200
    assert anon_client.get("/v1/baselines").status_code == 200
    assert anon_client.get("/v1/datasets").status_code == 200

    # 写入口(基线批准)仍要求账号,不能借"匿名只读"绕过。
    denied = anon_client.post(
        "/v1/baselines/agent-core",
        json={"payload": {"version": 1}},
    )
    assert denied.status_code == 401


def test_dataset_catalog_and_detail(client: TestClient) -> None:
    """数据集来自仓库 evals/datasets;清单与详情都应可直接读。"""

    items = client.get("/v1/datasets", headers=AUTH).json()["items"]
    names = {item["name"] for item in items}
    assert {"agent-core", "retrieval-quality"} <= names
    agent_core = next(item for item in items if item["name"] == "agent-core")
    assert agent_core["valid"] is True
    assert agent_core["case_count"] >= 1
    assert "chat-direct" in agent_core["groups"]

    detail = client.get("/v1/datasets/agent-core", headers=AUTH)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["case_count"] == len(body["cases"])
    assert body["cases"][0]["graders"]
    assert isinstance(body["cases"][0]["input"], dict)

    missing = client.get("/v1/datasets/does-not-exist", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["code"] == "eval.dataset_not_found"
