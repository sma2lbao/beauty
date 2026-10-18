"""BFF 测试装配:不依赖数据库/网络。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from bff.core.config import Settings
from bff.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app(Settings(env="dev"))
    with TestClient(app) as test_client:
        yield test_client
