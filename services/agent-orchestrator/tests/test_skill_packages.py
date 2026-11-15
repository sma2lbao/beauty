"""P3 技能包:上传/校验/版本化/列表/删除(零网络)。

对象存储走 FakeWorkspace(注入 WorkspaceClient 协议),zip 在测试内用 zipfile
构造;fixture 由 tests/support/skill_packages.py 提供。run 创建时冻结快照与
对话绑定见 test_skill_packages_runs.py。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from common.errors import AppError
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from agent_orchestrator.services import skill_packages
from tests.conftest import ACCOUNT, OTHER, FakeWorkspace, _settings, verifier
from tests.support.skill_packages import (
    BASE_MANIFEST,
    SKILL_MD,
    build_zip,
    pkg_client,
    plain_zip,
    upload,
    workspace,
)

__all__ = ["pkg_client", "workspace"]


def test_upload_new_package(pkg_client: TestClient, workspace: FakeWorkspace) -> None:
    data = plain_zip()
    response = upload(pkg_client, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "team.weekly-report"
    assert body["title"] == "周报生成"
    assert body["latest_version"] == "1.0.0"
    assert len(body["versions"]) == 1
    version = body["versions"][0]
    assert version["version"] == "1.0.0"
    assert version["has_scripts"] is True
    assert version["entrypoint"] == ["python", "scripts/run.py"]
    assert version["runtime"] == "python"
    assert version["network"] == "deny"
    assert version["timeout_seconds"] == 120
    assert version["size_bytes"] == len(data)
    assert version["sha256"] == workspace.sha256

    put = workspace.puts[0]
    assert put["account_id"] == "t-1"
    assert put["filename"] == "weekly-report.zip"
    assert put["content_type"] == "application/zip"
    assert put["data"] == data

    listing = pkg_client.get("/api/v1/skills/packages", headers=ACCOUNT).json()
    assert [item["id"] for item in listing] == [body["id"]]
    detail = pkg_client.get(f"/api/v1/skills/packages/{body['id']}", headers=ACCOUNT)
    assert detail.status_code == 200
    assert detail.json()["slug"] == "team.weekly-report"
    missing = pkg_client.get("/api/v1/skills/packages/deadbeef", headers=ACCOUNT)
    assert missing.status_code == 404


def test_skills_list_merges_builtin_and_uploaded(
    pkg_client: TestClient, workspace: FakeWorkspace
) -> None:
    package = upload(pkg_client, plain_zip()).json()
    version_id = package["versions"][0]["id"]
    listing = pkg_client.get("/api/v1/skills", headers=ACCOUNT).json()
    by_id = {item["id"]: item for item in listing}
    uploaded = by_id["team.weekly-report"]
    assert uploaded["source"] == "uploaded"
    assert uploaded["has_scripts"] is True
    assert uploaded["package_id"] == package["id"]
    assert uploaded["version_id"] == version_id
    assert "skill_run" in uploaded["tools"]
    assert "read" in uploaded["tools"]
    assert uploaded["system_prompt"].startswith("# 周报生成")
    builtin = by_id["coding.repo-readonly-review"]
    assert builtin["source"] == "builtin"
    assert builtin["has_scripts"] is False

    detail = pkg_client.get("/api/v1/skills/team.weekly-report", headers=ACCOUNT)
    assert detail.status_code == 200
    assert detail.json()["source"] == "uploaded"
    missing = pkg_client.get("/api/v1/skills/no.such-skill", headers=ACCOUNT)
    assert missing.status_code == 404
    assert missing.json()["code"] == "skill.not_found"


def test_upload_second_version_refreshes_latest(
    pkg_client: TestClient, workspace: FakeWorkspace
) -> None:
    first = upload(pkg_client, plain_zip()).json()
    second = upload(
        pkg_client,
        plain_zip({**BASE_MANIFEST, "version": "2.0.0", "title": "周报生成 v2"}),
    ).json()
    assert second["id"] == first["id"]
    assert second["latest_version"] == "2.0.0"
    assert {version["version"] for version in second["versions"]} == {
        "1.0.0",
        "2.0.0",
    }
    assert second["versions"][0]["version"] == "2.0.0"
    assert second["title"] == "周报生成 v2"
    assert len(workspace.puts) == 2


def test_duplicate_version_conflict(pkg_client: TestClient) -> None:
    assert upload(pkg_client, plain_zip()).status_code == 201
    response = upload(pkg_client, plain_zip())
    assert response.status_code == 409
    assert response.json()["code"] == "skill.version_exists"


def test_builtin_id_conflict(pkg_client: TestClient) -> None:
    data = plain_zip({**BASE_MANIFEST, "id": "coding.repo-readonly-review"})
    response = upload(pkg_client, data)
    assert response.status_code == 409
    assert response.json()["code"] == "skill.id_conflict"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"not a zip", "skill.package_invalid"),
        (build_zip({"skill.json": "{}"}), "skill.skill_md_missing"),
        (
            build_zip({"SKILL.md": SKILL_MD, "../evil.txt": "x"}),
            "skill.path_invalid",
        ),
        (build_zip({"SKILL.md": SKILL_MD, "/abs.txt": "x"}), "skill.path_invalid"),
        (
            build_zip({"SKILL.md": SKILL_MD, "C:/abs.txt": "x"}),
            "skill.path_invalid",
        ),
        (
            build_zip({"SKILL.md": SKILL_MD}, duplicate=["SKILL.md"]),
            "skill.path_invalid",
        ),
        (
            build_zip({"SKILL.md": SKILL_MD}, symlinks={"link": "/etc/passwd"}),
            "skill.path_invalid",
        ),
        (
            build_zip({"SKILL.md": SKILL_MD, "skill.json": "{oops"}),
            "skill.manifest_invalid",
        ),
        (
            plain_zip({**BASE_MANIFEST, "runtime": "ruby"}),
            "skill.manifest_invalid",
        ),
        (
            plain_zip({**BASE_MANIFEST, "timeout_seconds": 9999}),
            "skill.manifest_invalid",
        ),
        (
            plain_zip({**BASE_MANIFEST, "entrypoint": "../run.py"}),
            "skill.manifest_invalid",
        ),
    ],
)
def test_rejects_invalid_packages(
    pkg_client: TestClient,
    workspace: FakeWorkspace,
    data: bytes,
    code: str,
) -> None:
    response = upload(pkg_client, data)
    assert response.status_code == 400, response.text
    assert response.json()["code"] == code
    assert workspace.puts == []  # 校验失败不落对象存储


@pytest.mark.parametrize(
    "name",
    ["../evil.txt", "/etc/passwd", "bad\x00name.txt", "C:evil.txt", "dir/../evil"],
)
def test_validate_entry_rejects_unsafe_paths(name: str) -> None:
    """zip 写入会截断 NUL 等异常名,路径校验单独单测覆盖。"""
    with pytest.raises(AppError) as info:
        skill_packages._validate_entry(name)
    assert info.value.code == "skill.path_invalid"


def test_validate_entry_normalizes_separators() -> None:
    assert skill_packages._validate_entry("scripts\\run.py") == "scripts/run.py"
    assert skill_packages._validate_entry("./SKILL.md") == "SKILL.md"


def test_single_top_directory_is_normalized(pkg_client: TestClient) -> None:
    data = build_zip(
        {
            "weekly/SKILL.md": SKILL_MD,
            "weekly/skill.json": json.dumps(BASE_MANIFEST),
        }
    )
    response = upload(pkg_client, data)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["versions"][0]["entrypoint"] == ["python", "scripts/run.py"]


def test_slug_and_title_fall_back_to_filename_and_heading(
    pkg_client: TestClient,
) -> None:
    data = build_zip({"SKILL.md": "# 我的技能\n\n说明\n"})
    response = upload(pkg_client, data, filename="My Weekly Skill.zip")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "my-weekly-skill"
    assert body["title"] == "我的技能"
    assert body["versions"][0]["entrypoint"] is None
    assert body["versions"][0]["has_scripts"] is False


def test_package_size_limits(
    pkg_client: TestClient, workspace: FakeWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(skill_packages, "MAX_FILES", 2)
    response = upload(
        pkg_client, build_zip({"SKILL.md": SKILL_MD, "a.txt": "a", "b.txt": "b"})
    )
    assert response.status_code == 400
    assert response.json()["code"] == "skill.package_too_large"

    monkeypatch.setattr(skill_packages, "MAX_FILES", 500)
    monkeypatch.setattr(skill_packages, "MAX_UNCOMPRESSED_BYTES", 10)
    response = upload(pkg_client, plain_zip())
    assert response.status_code == 400
    assert response.json()["code"] == "skill.package_too_large"

    monkeypatch.setattr(skill_packages, "MAX_UNCOMPRESSED_BYTES", 50 * 1024 * 1024)
    monkeypatch.setattr(skill_packages, "MAX_PACKAGE_BYTES", 10)
    response = upload(pkg_client, plain_zip())
    assert response.status_code == 400
    assert response.json()["code"] == "skill.package_too_large"
    assert workspace.puts == []


def test_upload_requires_object_storage(tmp_path: Path) -> None:
    """没装配 WorkspaceClient:明确 503,不静默把包丢进 DB。"""
    app = create_app(_settings(tmp_path, "no-workspace.db"), token_verifier=verifier())
    with TestClient(app) as client:
        response = upload(client, plain_zip())
    assert response.status_code == 503
    assert response.json()["code"] == "skill.storage_unavailable"


def test_delete_package_removes_versions_and_objects(
    pkg_client: TestClient, workspace: FakeWorkspace
) -> None:
    package = upload(pkg_client, plain_zip()).json()
    upload(pkg_client, plain_zip({**BASE_MANIFEST, "version": "2.0.0"}))
    object_keys = {put["key"] for put in workspace.puts}
    response = pkg_client.delete(
        f"/api/v1/skills/packages/{package['id']}", headers=ACCOUNT
    )
    assert response.status_code == 204
    assert pkg_client.get("/api/v1/skills/packages", headers=ACCOUNT).json() == []
    detail = pkg_client.get(f"/api/v1/skills/packages/{package['id']}", headers=ACCOUNT)
    assert detail.status_code == 404
    assert {key for _, key in workspace.deleted} == object_keys


def test_delete_package_ignores_object_delete_failure(
    pkg_client: TestClient, workspace: FakeWorkspace
) -> None:
    """best-effort 删对象:对象删失败不阻塞 DB 删除(留日志)。"""
    package = upload(pkg_client, plain_zip()).json()
    workspace.fail = True
    response = pkg_client.delete(
        f"/api/v1/skills/packages/{package['id']}", headers=ACCOUNT
    )
    assert response.status_code == 204
    assert pkg_client.get("/api/v1/skills/packages", headers=ACCOUNT).json() == []


def test_packages_are_account_scoped(
    pkg_client: TestClient, workspace: FakeWorkspace
) -> None:
    package = upload(pkg_client, plain_zip()).json()
    assert pkg_client.get("/api/v1/skills/packages", headers=OTHER).json() == []
    detail = pkg_client.get(f"/api/v1/skills/packages/{package['id']}", headers=OTHER)
    assert detail.status_code == 404
    run = pkg_client.post(
        "/api/v1/runs",
        json={"title": "x", "goal": "g", "skill_id": "team.weekly-report"},
        headers=OTHER,
    )
    assert run.status_code == 404
    other_skills = pkg_client.get("/api/v1/skills", headers=OTHER).json()
    assert "team.weekly-report" not in [item["id"] for item in other_skills]
    deleted = pkg_client.delete(
        f"/api/v1/skills/packages/{package['id']}", headers=OTHER
    )
    assert deleted.status_code == 404
