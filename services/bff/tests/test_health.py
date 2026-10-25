import pytest
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


def test_readyz_ready_when_config_complete(client: TestClient) -> None:
    """conftest 提供合法配置(session/内部密钥齐全)→ 200 {"status":"ready"}。"""
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.parametrize(
    "client",
    [{"session_secret": "dev-bff-session-secret-change-me"}],
    indirect=True,
)
def test_readyz_503_when_session_secret_left_at_dev_default(
    client: TestClient,
) -> None:
    """session_secret 留在本地兜底默认值 → 503,detail 说明原因。"""
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "session_secret" in response.json()["detail"]


@pytest.mark.parametrize(
    "client",
    [{"internal_secret": ""}],
    indirect=True,
)
def test_readyz_503_when_internal_secret_empty(client: TestClient) -> None:
    """internal_secret 为空(stream token 不可用)→ 503。"""
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "internal_secret" in response.json()["detail"]


def test_readyz_no_upstream_probe_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """READY_PROBE_UPSTREAMS 默认关闭:就绪判定不发任何外部请求。"""
    probe_calls: list[str] = []
    client_app = client.app
    original = client_app.state.upstream_client.get

    async def spy_get(url: str, **kwargs: object) -> object:
        probe_calls.append(url)
        return await original(url, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(client_app.state.upstream_client, "get", spy_get)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert probe_calls == []


@pytest.mark.parametrize(
    "client",
    [{"ready_probe_upstreams": True}],
    indirect=True,
)
def test_readyz_upstream_probe_503_when_upstream_down(client: TestClient) -> None:
    """开启探测且上游 /healthz 不可用(MockTransport 404)→ 503,detail 带上游名。"""
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "upstream" in response.json()["detail"]
