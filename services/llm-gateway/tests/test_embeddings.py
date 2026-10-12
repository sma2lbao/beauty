"""embeddings:litellm 全打桩;验证归一化、条数一致性、错误映射。"""

import litellm
from fastapi.testclient import TestClient


def _data(*vectors: list[float]):
    return [type("D", (), {"embedding": vector})() for vector in vectors]


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
                "usage": type(
                    "U",
                    (),
                    {"prompt_tokens": 7, "completion_tokens": 0, "total_tokens": 7},
                )(),
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
