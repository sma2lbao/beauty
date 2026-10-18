from fastapi.testclient import TestClient


def test_models_returns_default_model(client: TestClient) -> None:
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert body["default_model"]
    ids = [item["id"] for item in body["data"]]
    assert body["default_model"] in ids


def test_models_uses_configured_list() -> None:
    from llm_gateway.core.config import Settings
    from llm_gateway.main import create_app

    app = create_app(
        Settings(
            env="dev",
            default_model="deepseek/chat",
            available_models=["deepseek/chat", "qwen/plus"],
        )
    )
    with TestClient(app) as test_client:
        body = test_client.get("/v1/models").json()
    assert [item["id"] for item in body["data"]] == ["deepseek/chat", "qwen/plus"]
