"""测试装配:无 DB;模型调用一律 monkeypatch litellm,不发真实请求。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = create_app(Settings(env="dev"))
    with TestClient(app) as test_client:
        yield test_client
