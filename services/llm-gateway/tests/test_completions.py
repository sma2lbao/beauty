"""completions:litellm 全部打桩——不发真实请求、不耗 token。"""

import litellm
from fastapi.testclient import TestClient

BODY = {
    "messages": [
        {"role": "system", "content": "你是周报助手"},
        {"role": "user", "content": "汇总本周邮件"},
    ]
}


def _fake_response(content: str = "好的,周报如下"):
    message = type("M", (), {"content": content})()
    choice = type("C", (), {"message": message})()
    usage = type(
        "U",
        (),
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )()
    return type(
        "R",
        (),
        {"choices": [choice], "usage": usage, "model": "deepseek/deepseek-chat"},
    )()


def test_completion_success_default_model(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post("/v1/completions", json=BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "好的,周报如下"
    assert data["model"] == "deepseek/deepseek-chat"
    assert data["usage"]["total_tokens"] == 15
    assert captured["model"] == "deepseek/deepseek-chat"  # 不传 model 走默认路由


def test_completion_explicit_model(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _fake_response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post("/v1/completions", json={**BODY, "model": "gpt-4o-mini"})
    assert resp.status_code == 200
    assert captured["model"] == "gpt-4o-mini"


def test_completion_upstream_error_maps_502(client: TestClient, monkeypatch) -> None:
    async def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(litellm, "acompletion", boom)
    resp = client.post("/v1/completions", json=BODY)
    assert resp.status_code == 502
    assert resp.json()["code"] == "llm.upstream_error"


def test_completion_validation(client: TestClient) -> None:
    assert client.post("/v1/completions", json={"messages": []}).status_code == 422
    resp = client.post(
        "/v1/completions", json={"messages": [{"role": "hacker", "content": "x"}]}
    )
    assert resp.status_code == 422
