"""测试装配:每个测试一个独立的 sqlite 文件库,建表走 lifespan(auto_create_tables)。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_orchestrator.core.config import Settings
from agent_orchestrator.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        env="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        auto_create_tables=True,
        sse_poll_interval_seconds=0.05,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
