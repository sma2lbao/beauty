"""对话与工作区的关系:对话必须落在工作区里(工作区归属)。

三条入口各自的口径:
- 显式 workspace_id:先过工作区存在性校验(未知 → 404 workspace.not_found);
- 不给:落租户默认工作区;
- 工作区集成未启用:降级为归属为空,但绝不阻断建对话。

外加列表按工作区过滤、存量空归属的懒回填,以及 run 从对话继承工作区。
测试全程 aiosqlite 文件库 + FakeWorkspace 打桩,零 HTTP。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import TENANT, FakeWorkspace, _settings, verifier

#: FakeWorkspace 登记过的工作区(id 存在即校验通过,未登记即 404)
WS = {
    "ws-1": {"id": "ws-1", "name": "研发", "status": "active"},
    "ws-2": {"id": "ws-2", "name": "运营", "status": "active"},
}


def _client(
    tmp_path: Path, name: str, workspace: FakeWorkspace | None = None
) -> TestClient:
    """建客户端;workspace=None 即"工作区集成未启用"的部署形态。"""
    app = create_app(
        _settings(tmp_path, name),
        workspace_client=workspace,
        token_verifier=verifier(),
    )
    return TestClient(app)


def _create(client: TestClient, payload: dict | None = None) -> dict:
    resp = client.post("/api/v1/conversations", json=payload or {}, headers=TENANT)
    assert resp.status_code == 201
    return resp.json()


def _detail(client: TestClient, conversation_id: str) -> dict:
    resp = client.get(f"/api/v1/conversations/{conversation_id}", headers=TENANT)
    assert resp.status_code == 200
    return resp.json()


def test_explicit_workspace_is_validated_and_persisted(tmp_path: Path) -> None:
    workspace = FakeWorkspace(workspaces=WS)
    with _client(tmp_path, "conv-ws-explicit.db", workspace) as client:
        created = _create(client, {"workspace_id": "ws-1"})

    assert created["workspace_id"] == "ws-1"
    # 显式 id 一定过一遍存在性校验,而不是盲信入参
    assert workspace.workspace_gets == [("t-1", "ws-1")]

    with _client(tmp_path, "conv-ws-explicit.db", workspace) as client:
        assert _detail(client, created["id"])["workspace_id"] == "ws-1"


def test_unknown_workspace_is_404(tmp_path: Path) -> None:
    with _client(
        tmp_path, "conv-ws-unknown.db", FakeWorkspace(workspaces=WS)
    ) as client:
        resp = client.post(
            "/api/v1/conversations", json={"workspace_id": "ws-404"}, headers=TENANT
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "workspace.not_found"


def test_missing_workspace_falls_back_to_default(tmp_path: Path) -> None:
    workspace = FakeWorkspace(workspaces=WS)
    with _client(tmp_path, "conv-ws-default.db", workspace) as client:
        created = _create(client)

    assert created["workspace_id"] == "ws-default"
    assert workspace.default_calls == ["t-1"]
    assert workspace.workspace_gets == []  # 没给 id 就不必按 id 校验


def test_explicit_workspace_id_accepted_unverified_when_disabled(
    tmp_path: Path,
) -> None:
    """工作区集成没开:无法校验存在性,但显式意图照落库,不阻断建对话。"""
    with _client(tmp_path, "conv-ws-unverified.db") as client:
        created = _create(client, {"workspace_id": "ws-x"})

    assert created["workspace_id"] == "ws-x"


def test_workspace_upstream_error_is_502(tmp_path: Path) -> None:
    """显式指定工作区但工作区服务不可达:502,不静默改归属。"""
    with _client(tmp_path, "conv-ws-upstream.db", FakeWorkspace(fail=True)) as client:
        resp = client.post(
            "/api/v1/conversations", json={"workspace_id": "ws-1"}, headers=TENANT
        )

    assert resp.status_code == 502
    assert resp.json()["code"] == "workspace.upstream_error"


def test_default_workspace_failure_degrades_to_null(tmp_path: Path) -> None:
    """默认工作区取不到时建对话仍成功,只是归属为空(后续可被回填)。"""
    with _client(tmp_path, "conv-ws-degrade.db", FakeWorkspace(fail=True)) as client:
        created = _create(client)

    assert created["workspace_id"] is None


def test_conversation_without_workspace_client_has_null_workspace(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, "conv-ws-none.db") as client:
        created = _create(client)

    assert created["workspace_id"] is None


def test_list_filters_by_workspace(tmp_path: Path) -> None:
    workspace = FakeWorkspace(workspaces=WS)
    with _client(tmp_path, "conv-ws-list.db", workspace) as client:
        first = _create(client, {"workspace_id": "ws-1"})
        _create(client, {"workspace_id": "ws-2"})
        items = client.get(
            "/api/v1/conversations?workspace_id=ws-1", headers=TENANT
        ).json()["items"]

    assert [item["id"] for item in items] == [first["id"]]
    # 过滤时仍解析一次默认工作区用来比对(决定是否回填),但非默认工作区不回填
    assert workspace.default_calls == ["t-1"]


def test_list_filter_default_workspace_backfills_legacy(tmp_path: Path) -> None:
    """按默认工作区过滤:存量空归属会话先补到该工作区,首屏即可见。"""
    with _client(tmp_path, "conv-ws-filter-default.db") as client:
        legacy = _create(client)  # 工作区集成未启用 → 归属为空

    workspace = FakeWorkspace(default_id="ws-default")
    with _client(tmp_path, "conv-ws-filter-default.db", workspace) as client:
        items = client.get(
            "/api/v1/conversations?workspace_id=ws-default", headers=TENANT
        ).json()["items"]

    assert [item["id"] for item in items] == [legacy["id"]]
    assert [item["workspace_id"] for item in items] == ["ws-default"]
    assert workspace.default_calls == ["t-1"]


def test_list_filter_other_workspace_keeps_legacy_null(tmp_path: Path) -> None:
    """按非默认工作区过滤:不搬动空归属会话,也不让它们出现在结果里。"""
    with _client(tmp_path, "conv-ws-filter-other.db") as client:
        legacy = _create(client)

    workspace = FakeWorkspace(workspaces=WS, default_id="ws-default")
    with _client(tmp_path, "conv-ws-filter-other.db", workspace) as client:
        current = _create(client, {"workspace_id": "ws-1"})
        items = client.get(
            "/api/v1/conversations?workspace_id=ws-1", headers=TENANT
        ).json()["items"]
        legacy_row = _detail(client, legacy["id"])

    assert [item["id"] for item in items] == [current["id"]]
    # 空归属没有被搬进 ws-1:旧会话仍在"默认工作区"口径下,过滤结果不泄漏
    assert legacy_row["workspace_id"] is None


def test_list_backfills_legacy_conversations(tmp_path: Path) -> None:
    """存量会话(归属为空)在列表首次访问时补到默认工作区。"""
    with _client(tmp_path, "conv-ws-backfill.db") as client:
        legacy = _create(client)
        assert legacy["workspace_id"] is None

    # 换成启用了工作区集成的部署,还是同一个库
    workspace = FakeWorkspace(default_id="ws-default")
    with _client(tmp_path, "conv-ws-backfill.db", workspace) as client:
        items = client.get("/api/v1/conversations", headers=TENANT).json()["items"]
        assert [item["workspace_id"] for item in items] == ["ws-default"]
        assert workspace.default_calls == ["t-1"]
        assert _detail(client, legacy["id"])["workspace_id"] == "ws-default"


def test_run_created_from_conversation_borrows_workspace(tmp_path: Path) -> None:
    """run 从对话继承工作区:对话在工作区里,它触发的 run 也在。"""
    workspace = FakeWorkspace(workspaces=WS)
    with _client(tmp_path, "conv-ws-run.db", workspace) as client:
        conversation = _create(client, {"workspace_id": "ws-1"})
        resp = client.post(
            "/api/v1/runs",
            json={
                "title": "周报",
                "goal": "整理本周日程",
                "acceptance_criteria": ["日程已整理"],
                "conversation_id": conversation["id"],
            },
            headers=TENANT,
        )
        assert resp.status_code == 201
        run_id = resp.json()["id"]
        stored = client.get(f"/api/v1/runs/{run_id}", headers=TENANT).json()

    assert resp.json()["workspace_id"] == "ws-1"
    assert stored["workspace_id"] == "ws-1"


def test_explicit_run_workspace_wins_over_conversation(tmp_path: Path) -> None:
    workspace = FakeWorkspace(workspaces=WS)
    with _client(tmp_path, "conv-ws-run-override.db", workspace) as client:
        conversation = _create(client, {"workspace_id": "ws-1"})
        resp = client.post(
            "/api/v1/runs",
            json={
                "title": "周报",
                "goal": "整理本周日程",
                "acceptance_criteria": ["日程已整理"],
                "conversation_id": conversation["id"],
                "workspace_id": "ws-2",
            },
            headers=TENANT,
        )

    assert resp.status_code == 201
    assert resp.json()["workspace_id"] == "ws-2"


def test_message_task_path_run_carries_conversation_workspace(
    tmp_path: Path,
) -> None:
    """对话里发复杂任务建的 run 与对话同工作区(stub 路径由服务端直接建 run)。"""
    workspace = FakeWorkspace(workspaces=WS)
    with _client(tmp_path, "conv-ws-message.db", workspace) as client:
        conversation = _create(client, {"workspace_id": "ws-1"})
        result = client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": "把本周日程整理成周报", "mode": "auto"},
            headers=TENANT,
        ).json()
        assert result["mode"] == "run_created"
        run = client.get(f"/api/v1/runs/{result['run']['id']}", headers=TENANT).json()

    assert run["workspace_id"] == "ws-1"
