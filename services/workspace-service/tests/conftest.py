"""测试装配:每用例独立 sqlite 库 + 独立对象存储目录(默认 filesystem)。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workspace.core.config import Settings
from workspace.main import create_app

TENANT = {"X-Tenant-Id": "t-1"}
OTHER = {"X-Tenant-Id": "t-2"}


def _settings(tmp_path: Path, name: str = "test.db", **overrides) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/{name}",
        auto_create_tables=True,
        artifact_root=str(tmp_path / "objects"),
        **overrides,
    )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def _create_artifact(
    client: TestClient, title: str = "周报", content: str = "# 周报\n要点", **extra
):
    return client.post(
        "/v1/artifacts",
        json={"title": title, "content": content, **extra},
        headers=TENANT,
    )
