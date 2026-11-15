"""P3 技能包测试共用装配:zip 构造、上传助手与客户端 fixture(零网络)。

对象存储走 FakeWorkspace(注入 WorkspaceClient 协议),zip 在测试内用 zipfile
构造。
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.main import create_app
from tests.conftest import ACCOUNT, FakeWorkspace, _settings, verifier

SKILL_MD = "# 周报生成\n\n按模板整理本周日程。\n"
BASE_MANIFEST: dict[str, Any] = {
    "id": "team.weekly-report",
    "version": "1.0.0",
    "title": "周报生成",
    "description": "整理本周日程并生成周报",
    "entrypoint": ["python", "scripts/run.py"],
    "runtime": "python",
    "network": "deny",
    "timeout_seconds": 120,
    "tools": ["read"],
}


def build_zip(
    files: dict[str, str | bytes],
    *,
    symlinks: dict[str, str] | None = None,
    duplicate: list[str] | None = None,
) -> bytes:
    """测试内构造 zip;symlinks/duplicate 用于构造恶意/异常包。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name in duplicate or []:
            archive.writestr(name, "duplicate")
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16  # S_IFLNK
            archive.writestr(info, target)
    return buffer.getvalue()


def plain_zip(
    manifest: dict[str, Any] | None = BASE_MANIFEST,
    **extra_files: str,
) -> bytes:
    files: dict[str, str | bytes] = {"SKILL.md": SKILL_MD}
    if manifest is not None:
        files["skill.json"] = json.dumps(manifest)
    files.update(extra_files)
    return build_zip(files)


def upload(
    client: TestClient,
    data: bytes,
    filename: str = "weekly-report.zip",
    headers: dict[str, str] = ACCOUNT,
):
    return client.post(
        "/api/v1/skills/packages",
        files={"file": (filename, data, "application/zip")},
        headers=headers,
    )


@pytest.fixture()
def workspace() -> FakeWorkspace:
    return FakeWorkspace()


@pytest.fixture()
def pkg_client(tmp_path: Path, workspace: FakeWorkspace) -> Iterator[TestClient]:
    app = create_app(
        _settings(tmp_path, "packages.db"),
        workspace_client=workspace,
        token_verifier=verifier(),
    )
    with TestClient(app) as client:
        yield client
