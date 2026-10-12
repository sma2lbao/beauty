from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz(client: TestClient) -> None:
    assert client.get("/readyz").json() == {"status": "ready"}
