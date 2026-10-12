"""测试装配:每用例独立 sqlite 库;鉴权关闭,用临时租户头。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from account.core.config import Settings
from account.main import create_app

TENANT = {"X-Tenant-Id": "org-1"}


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


def _create_user(
    client: TestClient, subject: str = "idp|u1", tenant: dict | None = None
):
    return client.post(
        "/v1/users",
        json={"subject": subject, "email": f"{subject}@example.com", "role": "owner"},
        headers=tenant or TENANT,
    )
