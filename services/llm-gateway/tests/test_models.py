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


def test_models_whitelist_rejects_unknown_model() -> None:
    """§3/A 白名单:列表非空时,不在列表内的模型在入口层直接 400。"""
    from llm_gateway.core.config import Settings
    from llm_gateway.main import create_app

    app = create_app(
        Settings(
            env="dev",
            available_models=["deepseek/chat"],
            fallback_models=[],
            _env_file=None,
        )
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/v1/completions",
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert response.status_code == 400
    assert response.json()["code"] == "llm.model_not_allowed"
