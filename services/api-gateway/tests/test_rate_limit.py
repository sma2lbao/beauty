from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import login


@pytest.mark.parametrize("client", [{"rate_limit_per_minute": 2}], indirect=True)
def test_rate_limit_returns_429(client: TestClient) -> None:
    login(client)
    assert client.get("/api/v1/runs").status_code == 200
    assert client.get("/api/v1/runs").status_code == 200
    limited = client.get("/api/v1/runs")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
