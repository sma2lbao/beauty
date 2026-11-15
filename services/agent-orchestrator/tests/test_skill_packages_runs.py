"""P3 技能包:run 创建时冻结快照、工具声明与对话绑定(零网络)。

fixture 由 tests/support/skill_packages.py 提供;包管理面(上传/校验/删除)
见 test_skill_packages.py。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import ACCOUNT, FakeWorkspace, _settings, verifier
from tests.support.skill_packages import (
    BASE_MANIFEST,
    pkg_client,
    plain_zip,
    upload,
    workspace,
)

__all__ = ["pkg_client", "workspace"]


def test_run_freezes_uploaded_skill_snapshot(
    tmp_path: Path, workspace: FakeWorkspace
) -> None:
    app = create_app(
        _settings(tmp_path, "run-skill.db"),
        workspace_client=workspace,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        package = upload(client, plain_zip()).json()
        version = package["versions"][0]
        response = client.post(
            "/api/v1/runs",
            json={
                "title": "周报",
                "goal": "生成周报",
                "skill_id": "team.weekly-report",
            },
            headers=ACCOUNT,
        )
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["skill_id"] == "team.weekly-report"
        # 上传新版本:已建 run 的快照不能被改写
        upload(client, plain_zip({**BASE_MANIFEST, "version": "2.0.0"}))

    conn = sqlite3.connect(tmp_path / "run-skill.db")
    try:
        row = conn.execute(
            "SELECT skill_id, skill_version_id, skill_object_key, skill_sha256, "
            "skill_entrypoint, skill_runtime, skill_network, skill_timeout_seconds "
            "FROM runs WHERE id = ?",
            (run["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "team.weekly-report"
    assert row[1] == version["id"]
    assert row[2] == workspace.puts[0]["key"]
    assert row[3] == workspace.sha256
    assert json.loads(row[4]) == ["python", "scripts/run.py"]
    assert row[5] == "python"
    assert row[6] == "deny"
    assert row[7] == 120


def test_undeclared_tools_is_unrestricted(
    tmp_path: Path, workspace: FakeWorkspace
) -> None:
    """未声明 tools 不被物化成空表:API 返回 null、run 快照 skill_tools 为 NULL;

    显式 ``tools: []`` 仍是"不用工具",接口与快照都保留空表(两者必须可区分)。
    """
    app = create_app(
        _settings(tmp_path, "unrestricted.db"),
        workspace_client=workspace,
        token_verifier=verifier(),
    )
    undeclared = {key: value for key, value in BASE_MANIFEST.items() if key != "tools"}
    explicit_none = {
        key: value
        for key, value in BASE_MANIFEST.items()
        if key not in {"tools", "entrypoint"}
    }
    explicit_none.update(
        {"id": "team.no-tools", "title": "不用工具的技能", "tools": []}
    )
    with TestClient(app) as client:
        assert upload(client, plain_zip(undeclared)).status_code == 201
        no_tools = upload(client, plain_zip(explicit_none), filename="no-tools.zip")
        assert no_tools.status_code == 201, no_tools.text

        skills = {
            item["id"]: item
            for item in client.get("/api/v1/skills", headers=ACCOUNT).json()
        }
        assert skills["team.weekly-report"]["tools"] is None
        assert skills["team.no-tools"]["tools"] == []

        run_ids = {}
        for skill_id in ("team.weekly-report", "team.no-tools"):
            response = client.post(
                "/api/v1/runs",
                json={"title": skill_id, "goal": "g", "skill_id": skill_id},
                headers=ACCOUNT,
            )
            assert response.status_code == 201, response.text
            run_ids[skill_id] = response.json()["id"]

    conn = sqlite3.connect(tmp_path / "unrestricted.db")
    try:
        frozen = dict(
            conn.execute(
                "SELECT id, skill_tools FROM runs WHERE id IN (?, ?)",
                (run_ids["team.weekly-report"], run_ids["team.no-tools"]),
            ).fetchall()
        )
    finally:
        conn.close()
    # JSON 列:未声明落 JSON null(ORM 读回 None),显式空表落 []。
    assert json.loads(frozen[run_ids["team.weekly-report"]]) is None
    assert json.loads(frozen[run_ids["team.no-tools"]]) == []


def test_run_with_unknown_skill_not_found(pkg_client: TestClient) -> None:
    response = pkg_client.post(
        "/api/v1/runs",
        json={"title": "x", "goal": "g", "skill_id": "team.nope"},
        headers=ACCOUNT,
    )
    assert response.status_code == 404
    assert response.json()["code"] == "skill.not_found"


def test_message_can_bind_uploaded_skill(pkg_client: TestClient) -> None:
    """对话任务路径与 run 创建共用 resolve_skill(上传包也可绑定)。"""
    upload(pkg_client, plain_zip())
    conversation = pkg_client.post(
        "/api/v1/conversations", json={}, headers=ACCOUNT
    ).json()
    response = pkg_client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={
            "content": "生成周报",
            "mode": "task",
            "skill_id": "team.weekly-report",
        },
        headers=ACCOUNT,
    )
    assert response.status_code == 201, response.text
    assert response.json()["run"]["skill_id"] == "team.weekly-report"

    missing = pkg_client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "x", "mode": "task", "skill_id": "team.nope"},
        headers=ACCOUNT,
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "skill.not_found"


def test_stream_message_can_bind_uploaded_skill(pkg_client: TestClient) -> None:
    """SSE 路径在开流前解析技能(未知 id 直接 404,不进流)。"""
    upload(pkg_client, plain_zip())
    conversation = pkg_client.post(
        "/api/v1/conversations", json={}, headers=ACCOUNT
    ).json()
    with pkg_client.stream(
        "POST",
        f"/api/v1/conversations/{conversation['id']}/messages/stream",
        json={
            "content": "生成周报",
            "mode": "task",
            "skill_id": "team.weekly-report",
        },
        headers=ACCOUNT,
    ) as response:
        assert response.status_code == 200
        text = response.read().decode()
    assert "team.weekly-report" in text

    with pkg_client.stream(
        "POST",
        f"/api/v1/conversations/{conversation['id']}/messages/stream",
        json={"content": "x", "mode": "task", "skill_id": "team.nope"},
        headers=ACCOUNT,
    ) as response:
        assert response.status_code == 404
