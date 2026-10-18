from fastapi.testclient import TestClient

from tests.conftest import login


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_api_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "bff"


def test_unknown_route_is_normalized(client: TestClient) -> None:
    login(client)
    response = client.get("/api/not-found")
    assert response.status_code == 404
