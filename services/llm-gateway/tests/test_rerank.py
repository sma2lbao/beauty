"""rerank:litellm `arerank` 全打桩;验证路由、归一化、凭据回退、错误映射。

参照百炼 text-rerank API:一次请求 query + documents,回 results[].index/relevance_score,
响应固定按 relevance_score 降序(gte-rerank / qwen3-rerank 都这样)。
"""

from __future__ import annotations

import litellm
import pytest
from common.service_testkit import build_settings
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app


def _settings(**overrides) -> Settings:
    values = {"byok_enabled": False, "internal_secret": ""}
    values.update(overrides)
    return build_settings(Settings, **values)


def _response(scores: list[tuple[int, float]], *, model: str = "qwen3-rerank"):
    """仿 litellm RerankResponse:dict 形状(服务层 _get 兼容对象与 dict)。"""
    return {
        "id": "req-1",
        "model": model,
        "results": [
            {"index": index, "relevance_score": score} for index, score in scores
        ],
        "meta": {"billed_units": {"total_tokens": 128}},
    }


@pytest.fixture(name="credential_client")
def _credential_client():
    """带平台凭据的客户端:验证 API_KEY/API_BASE 真的传到 litellm。"""
    settings = _settings(
        api_key="sk-platform",
        api_base="https://dashscope.test/compatible-mode/v1",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_rerank_success_sorted_and_defaults_model(
    client: TestClient, monkeypatch
) -> None:
    captured: dict = {}

    async def fake_arerank(**kwargs):
        captured.update(kwargs)
        return _response([(0, 0.12), (1, 0.91), (2, 0.44)])

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    resp = client.post(
        "/v1/rerank",
        json={"query": "合同违约怎么赔", "documents": ["甲", "乙", "丙"]},
    )

    assert resp.status_code == 200
    body = resp.json()
    # 上游已降序,网关仍按分数重排一次:响应顺序是契约
    assert [item["index"] for item in body["results"]] == [1, 2, 0]
    assert body["results"][0]["relevance_score"] == pytest.approx(0.91)
    assert body["model"] == "qwen3-rerank"
    assert body["usage"]["total_tokens"] == 128
    assert body["usage"]["completion_tokens"] == 0
    assert captured["model"] == "dashscope/qwen3-rerank"  # 不传走默认
    assert captured["query"] == "合同违约怎么赔"
    assert captured["documents"] == ["甲", "乙", "丙"]
    assert captured["return_documents"] is False
    assert captured["top_n"] is None


def test_rerank_forwards_top_n_and_model(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_arerank(**kwargs):
        captured.update(kwargs)
        return _response([(0, 0.5), (1, 0.6)], model="qwen3.7-text-rerank")

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    resp = client.post(
        "/v1/rerank",
        json={
            "model": "dashscope/qwen3.7-text-rerank",
            "query": "q",
            "documents": ["a", "b"],
            "top_n": 1,
        },
    )

    assert resp.status_code == 200
    assert captured["model"] == "dashscope/qwen3.7-text-rerank"
    assert captured["top_n"] == 1
    assert resp.json()["model"] == "qwen3.7-text-rerank"


def test_rerank_validation(client: TestClient) -> None:
    assert (
        client.post("/v1/rerank", json={"query": "", "documents": ["a"]}).status_code
        == 422
    )
    assert (
        client.post("/v1/rerank", json={"query": "q", "documents": []}).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/rerank",
            json={"query": "q", "documents": ["a"], "top_n": 0},
        ).status_code
        == 422
    )
    too_many = [f"d{index}" for index in range(501)]
    assert (
        client.post(
            "/v1/rerank", json={"query": "q", "documents": too_many}
        ).status_code
        == 422
    )


def test_rerank_upstream_error_maps_502(client: TestClient, monkeypatch) -> None:
    async def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(litellm, "arerank", boom)
    resp = client.post("/v1/rerank", json={"query": "q", "documents": ["a"]})

    assert resp.status_code == 502
    body = resp.json()
    assert body["code"] == "llm.rerank_error"
    assert "provider down" in body["message"]


def test_rerank_index_out_of_range_rejected(client: TestClient, monkeypatch) -> None:
    """越界下标必须报错:静默丢弃会让调用方的分数与候选错位。"""

    async def fake_arerank(**kwargs):
        return _response([(0, 0.9), (5, 0.8)])

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    resp = client.post("/v1/rerank", json={"query": "q", "documents": ["a", "b"]})

    assert resp.status_code == 502
    assert resp.json()["code"] == "llm.rerank_error"


def test_rerank_empty_results_rejected(client: TestClient, monkeypatch) -> None:
    async def fake_arerank(**kwargs):
        return {"results": [], "model": "qwen3-rerank", "meta": None}

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    resp = client.post("/v1/rerank", json={"query": "q", "documents": ["a"]})

    assert resp.status_code == 502
    assert resp.json()["code"] == "llm.rerank_error"


def test_rerank_without_usage_does_not_fabricate(
    client: TestClient, monkeypatch
) -> None:
    async def fake_arerank(**kwargs):
        return {"results": [{"index": 0, "relevance_score": 1.0}], "model": "m"}

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    resp = client.post("/v1/rerank", json={"query": "q", "documents": ["a"]})

    assert resp.status_code == 200
    assert resp.json()["usage"] is None


def test_rerank_platform_credentials_passed(
    credential_client: TestClient, monkeypatch
) -> None:
    """平台 API_KEY/API_BASE 显式传给 litellm(平台内配置必须自洽)。"""
    captured: dict = {}

    async def fake_arerank(**kwargs):
        captured.update(kwargs)
        return _response([(0, 0.5)])

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    resp = credential_client.post(
        "/v1/rerank",
        json={"query": "q", "documents": ["a"], "model": "dashscope/qwen3-rerank"},
    )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"
    assert captured["api_base"] == "https://dashscope.test/compatible-mode/v1"


def test_rerank_reuses_embedding_credentials(tmp_path, monkeypatch) -> None:
    """对话在别家、嵌入+重排在百炼:没单独配重排时复用嵌入凭据。"""
    captured: dict = {}

    async def fake_arerank(**kwargs):
        captured.update(kwargs)
        return _response([(0, 0.5)])

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    settings = _settings(
        api_key="sk-deepseek",
        api_base="https://api.deepseek.test/v1",
        embedding_api_key="sk-bailian",
        embedding_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    with TestClient(create_app(settings)) as custom:
        resp = custom.post(
            "/v1/rerank",
            json={"query": "q", "documents": ["a"], "model": "dashscope/qwen3-rerank"},
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-bailian"
    assert captured["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_rerank_dedicated_credentials_override_embedding(tmp_path, monkeypatch) -> None:
    """重排与嵌入不同家(Cohere 重排 + 百炼嵌入)时,专用凭据优先。"""
    captured: dict = {}

    async def fake_arerank(**kwargs):
        captured.update(kwargs)
        return _response([(0, 0.5)])

    monkeypatch.setattr(litellm, "arerank", fake_arerank)
    settings = _settings(
        embedding_api_key="sk-bailian",
        embedding_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        rerank_api_key="sk-cohere",
        rerank_api_base="https://api.cohere.test/v2",
    )
    with TestClient(create_app(settings)) as custom:
        resp = custom.post(
            "/v1/rerank",
            json={"query": "q", "documents": ["a"], "model": "cohere/rerank-v3"},
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-cohere"
    assert captured["api_base"] == "https://api.cohere.test/v2"
