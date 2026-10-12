"""测试装配:每用例独立 sqlite 库;默认用本地嵌入提供方(零网络)。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from knowledge.core.config import Settings
from knowledge.main import create_app

TENANT = {"X-Tenant-Id": "t-1"}


def _settings(tmp_path: Path, name: str = "test.db", **overrides) -> Settings:
    return Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/{name}",
        auto_create_tables=True,
        **overrides,
    )


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def _ingest(client: TestClient, title: str, content: str, tenant: dict | None = None):
    return client.post(
        "/v1/knowledge/documents",
        json={"title": title, "content": content},
        headers=tenant or TENANT,
    )
