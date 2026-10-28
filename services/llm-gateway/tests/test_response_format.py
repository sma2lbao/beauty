"""response_format(结构化输出,model-routing §5.1):合法值原样透传,非法值 400。"""

from __future__ import annotations

from typing import Any

import litellm
from fastapi.testclient import TestClient

from tests.fakes import chunk, response, sse_frames, stream

BODY = {
    "messages": [{"role": "user", "content": "输出 JSON"}],
}


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    async def fake_acompletion(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    return captured


def test_json_object_passthrough(client: TestClient, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    payload = {**BODY, "response_format": {"type": "json_object"}}
    resp = client.post("/v1/completions", json=payload)

    assert resp.status_code == 200
    assert captured["response_format"] == {"type": "json_object"}


def test_json_schema_passthrough(client: TestClient, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "weekly_report",
            "schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
        },
    }
    resp = client.post("/v1/completions", json={**BODY, "response_format": schema})

    assert resp.status_code == 200
    assert captured["response_format"] == schema


def test_absent_response_format_not_passed(client: TestClient, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    resp = client.post("/v1/completions", json=BODY)

    assert resp.status_code == 200
    assert "response_format" not in captured


def test_invalid_type_is_400(client: TestClient, monkeypatch) -> None:
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append("called")
        return response()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post(
        "/v1/completions", json={**BODY, "response_format": {"type": "text"}}
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.invalid_response_format"
    assert called == []  # 校验先于上游调用


def test_json_schema_without_object_is_400(client: TestClient) -> None:
    for response_format in [
        {"type": "json_schema"},
        {"type": "json_schema", "json_schema": "not-a-dict"},
        {"type": "json_schema", "json_schema": ["x"]},
    ]:
        resp = client.post(
            "/v1/completions", json={**BODY, "response_format": response_format}
        )
        assert resp.status_code == 400, response_format
        assert resp.json()["code"] == "llm.invalid_response_format"


def test_stream_invalid_response_format_is_400_before_sse(
    client: TestClient, monkeypatch
) -> None:
    """流式入口同样先校验:非法参数要拿到 400,而不是一帧 error。"""
    called: list[str] = []

    async def fake_acompletion(**kwargs: Any) -> Any:
        called.append("called")
        return await stream([chunk("x")])(**kwargs)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    resp = client.post(
        "/v1/completions/stream",
        json={**BODY, "response_format": {"type": "json_objectx"}},
    )

    assert resp.status_code == 400
    assert resp.json()["code"] == "llm.invalid_response_format"
    assert called == []


def test_stream_json_object_passthrough(client: TestClient, monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", stream([chunk("{}")], captured))
    resp = client.post(
        "/v1/completions/stream",
        json={**BODY, "response_format": {"type": "json_object"}},
    )

    assert resp.status_code == 200
    assert captured["response_format"] == {"type": "json_object"}
    assert sse_frames(resp.text)[-1]["done"] is True
