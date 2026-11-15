"""BYOK 在 API 层的注入:completions/stream/embeddings 覆盖或回退平台 key。

litellm 全打桩、account-service 走 httpx.MockTransport:零网络。
"""

from __future__ import annotations

import httpx
import litellm

from tests.byok_support import (
    BODY,
    HEADERS,
    KEY,
    SECRET,
    AccountStub,
    _client_with_stub,
    _fake_completion,
)


def test_api_completion_uses_byok_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    assert captured["api_key"] == f"{KEY}-t1"  # BYOK 覆盖平台 key
    assert "api_base" not in captured  # 平台没配 api_base 时不传
    assert stub.requests[0].headers["X-Account-Id"] == "t1"


def test_api_completion_marks_billing_source_byok(monkeypatch) -> None:
    """命中账号 BYOK:usage 带 billing_source=byok(成本仍按价目表估算)。"""
    stub = AccountStub()
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        message = type("M", (), {"content": "ok", "tool_calls": None})()
        usage = type(
            "U",
            (),
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": None,
            },
        )()
        return type(
            "R",
            (),
            {
                "choices": [type("C", (), {"message": message})()],
                "usage": usage,
                "model": "deepseek/deepseek-chat",
            },
        )()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["billing_source"] == "byok"
    assert usage["cost_usd"] > 0


def test_api_completion_without_account_uses_platform_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post(
            "/v1/completions", json=BODY, headers={"X-Internal-Secret": SECRET}
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"
    assert stub.requests == []  # 无账号头 = 不查 account


def test_api_completion_missing_byok_falls_back(monkeypatch) -> None:
    stub = AccountStub(lambda request: httpx.Response(404))
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"


def test_api_completion_without_provider_prefix_skips_byok(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}
    monkeypatch.setattr(litellm, "acompletion", _fake_completion(captured))

    with _client_with_stub(stub) as client:
        resp = client.post(
            "/v1/completions",
            json={**BODY, "model": "gpt-4o-mini"},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"
    assert stub.requests == []  # 模型串没有 provider 前缀,不猜


def test_api_stream_uses_byok_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)

        async def chunks():
            delta = type("D", (), {"content": "你", "tool_calls": None})()
            yield type(
                "Chunk",
                (),
                {
                    "choices": [type("C", (), {"delta": delta})()],
                    "model": "deepseek/deepseek-chat",
                },
            )()

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    with _client_with_stub(stub) as client:
        resp = client.post("/v1/completions/stream", json=BODY, headers=HEADERS)

    assert resp.status_code == 200
    assert captured["api_key"] == f"{KEY}-t1"
    assert '"done":true' in resp.text  # 收尾帧已产出(SSE 已开始流式返回)


def test_api_embeddings_uses_byok_key(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return {
            "data": [{"embedding": [0.1, 0.2]}],
            "model": "openai/text-embedding-3-small",
            "usage": None,
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    with _client_with_stub(stub) as client:
        resp = client.post(
            "/v1/embeddings",
            json={"model": "openai/text-embedding-3-small", "input": ["hi"]},
            headers=HEADERS,
        )

    assert resp.status_code == 200
    assert captured["api_key"] == f"{KEY}-t1"
    assert captured["provider"] == "openai"  # 显式 provider 供 litellm 路由
    assert stub.requests[0].url.path == "/v1/byok/openai/secret"


def test_api_embeddings_without_provider_prefix_uses_platform(monkeypatch) -> None:
    stub = AccountStub()
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return {"data": [{"embedding": [0.1]}], "model": "bge-m3", "usage": None}

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    with _client_with_stub(stub) as client:
        resp = client.post("/v1/embeddings", json={"input": ["hi"]}, headers=HEADERS)

    assert resp.status_code == 200
    assert "api_key" not in captured  # 默认嵌入模型无前缀:不查 BYOK、不传平台 key
    assert stub.requests == []
