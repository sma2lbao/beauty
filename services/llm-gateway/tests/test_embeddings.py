"""embeddings:litellm 全打桩;验证归一化、分批、凭据透传、条数一致性、错误映射。"""

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


def _data(*vectors: list[float]):
    return [type("D", (), {"embedding": vector})() for vector in vectors]


def _usage(total: int):
    return type(
        "U",
        (),
        {"prompt_tokens": total, "completion_tokens": 0, "total_tokens": total},
    )()


@pytest.fixture(name="credential_client")
def _credential_client():
    """带平台凭据的客户端:验证 API_KEY/API_BASE 真的传到 litellm。"""
    settings = _settings(
        api_key="sk-platform",
        api_base="https://dashscope.test/compatible-mode/v1",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_embeddings_success(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return type(
            "R",
            (),
            {
                "data": _data([0.1, 0.2], [0.3, 0.4]),
                "model": "bge-m3",
                "usage": _usage(7),
            },
        )()

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    resp = client.post("/v1/embeddings", json={"input": ["甲", "乙"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
    assert body["model"] == "bge-m3"
    assert body["usage"]["total_tokens"] == 7
    assert captured["model"] == "bge-m3"  # 不传走 default_embedding_model


def test_embeddings_dict_shape_normalized(client: TestClient, monkeypatch) -> None:
    """部分提供商返回 dict 形状,也要能归一化。"""

    async def fake_aembedding(**kwargs):
        return {
            "data": [{"embedding": [1, 2, 3]}],
            "model": "bge-m3",
            "usage": None,
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    resp = client.post("/v1/embeddings", json={"input": ["x"]})

    assert resp.json()["embeddings"] == [[1.0, 2.0, 3.0]]
    assert resp.json()["usage"] is None  # 无 usage 时不编造


def test_embeddings_count_mismatch_rejected(client: TestClient, monkeypatch) -> None:
    async def fake_aembedding(**kwargs):
        return {"data": _data([0.1]), "model": "m", "usage": None}

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    resp = client.post("/v1/embeddings", json={"input": ["甲", "乙"]})

    assert resp.status_code == 502
    assert resp.json()["code"] == "llm.embedding_error"


def test_embeddings_upstream_error_maps_502(client: TestClient, monkeypatch) -> None:
    async def boom(**kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(litellm, "aembedding", boom)
    resp = client.post("/v1/embeddings", json={"input": ["x"]})

    assert resp.status_code == 502
    assert resp.json()["code"] == "llm.embedding_error"


def test_embeddings_validation(client: TestClient) -> None:
    assert client.post("/v1/embeddings", json={"input": []}).status_code == 422


def test_embeddings_batched_by_provider_row_limit(
    client: TestClient, monkeypatch
) -> None:
    """25 行按默认 10 行/批切 3 次(百炼 text-embedding-v4 只收 10 行)。

    调用方一次提交整篇文档的块,网关切开后**按原顺序**拼接向量、usage 累加。
    """
    calls: list[dict] = []

    async def fake_aembedding(**kwargs):
        calls.append(kwargs)
        return {
            "data": [
                {"embedding": [float(text.removeprefix("c"))]}
                for text in kwargs["input"]
            ],
            "model": kwargs["model"],
            "usage": _usage(3),
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    texts = [f"c{index}" for index in range(25)]
    resp = client.post(
        "/v1/embeddings",
        json={"input": texts, "model": "dashscope/text-embedding-v4"},
    )

    assert resp.status_code == 200
    assert [len(call["input"]) for call in calls] == [10, 10, 5]
    assert calls[0]["input"] == texts[:10]
    assert calls[2]["input"] == texts[20:]
    body = resp.json()
    assert body["embeddings"] == [[float(index)] for index in range(25)]
    assert body["usage"]["total_tokens"] == 9  # 3 批 × 3
    assert body["model"] == "dashscope/text-embedding-v4"


def test_embeddings_accepts_large_input_list(client: TestClient, monkeypatch) -> None:
    """整篇文档的块一次提交(远超旧的 64 行上限)不再 422:分批是网关的事。"""
    calls: list[dict] = []

    async def fake_aembedding(**kwargs):
        calls.append(kwargs)
        return {
            "data": [{"embedding": [0.0]} for _ in kwargs["input"]],
            "model": "m",
            "usage": None,
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    resp = client.post(
        "/v1/embeddings", json={"input": [f"c{index}" for index in range(130)]}
    )

    assert resp.status_code == 200
    assert len(calls) == 13  # 130 / 10
    assert len(resp.json()["embeddings"]) == 130


def test_embeddings_batch_size_is_configurable(tmp_path, monkeypatch) -> None:
    """批大小可配(换 provider 时上调);非正值退化为 1,不会死循环。"""
    calls: list[dict] = []

    async def fake_aembedding(**kwargs):
        calls.append(kwargs)
        return {
            "data": [{"embedding": [0.0]} for _ in kwargs["input"]],
            "model": "m",
            "usage": None,
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    with TestClient(create_app(_settings(embedding_batch_size=0))) as custom:
        resp = custom.post("/v1/embeddings", json={"input": ["a", "b"]})

    assert resp.status_code == 200
    assert [len(call["input"]) for call in calls] == [1, 1]


def test_embeddings_platform_credentials_passed(
    credential_client: TestClient, monkeypatch
) -> None:
    """平台 API_KEY/API_BASE 显式传给 litellm:百炼只能靠它(不依赖环境变量)。"""
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return {
            "data": [{"embedding": [0.1]}],
            "model": "dashscope/text-embedding-v4",
            "usage": None,
        }

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    resp = credential_client.post(
        "/v1/embeddings",
        json={"input": ["hi"], "model": "dashscope/text-embedding-v4"},
    )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-platform"
    assert captured["api_base"] == "https://dashscope.test/compatible-mode/v1"
    # provider 由 model 前缀路由,不能塞进 kwargs——litellm 会把它原样写进请求体
    assert "provider" not in captured


def test_embeddings_dedicated_credentials_override_platform(
    tmp_path, monkeypatch
) -> None:
    """对话与嵌入不同家(DeepSeek 对话 + 百炼嵌入):嵌入专用凭据必须优先。"""
    captured: dict = {}

    async def fake_aembedding(**kwargs):
        captured.update(kwargs)
        return {"data": [{"embedding": [0.1]}], "model": "m", "usage": None}

    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    settings = _settings(
        api_key="sk-deepseek",
        api_base="https://api.deepseek.test/v1",
        embedding_api_key="sk-bailian",
        embedding_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    with TestClient(create_app(settings)) as custom:
        resp = custom.post(
            "/v1/embeddings",
            json={"input": ["hi"], "model": "dashscope/text-embedding-v4"},
        )

    assert resp.status_code == 200
    assert captured["api_key"] == "sk-bailian"
    assert captured["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
