"""内部鉴权:配置 INTERNAL_SECRET 后模型出口必须带 X-Internal-Secret。

- 未配置(本地 dev)放行——现网/CI 必须配,否则内网任意进程都能烧模型;
- 配置后三个模型端点(mask:completions/stream/embeddings)一律 401
  auth.internal_forbidden;GET /v1/models 与探针豁免(只读元数据);
- 鉴权在 BYOK 解析与上游调用之前生效(未授权的请求不产生任何外部请求)。
"""

from __future__ import annotations

import litellm
import pytest
from common.service_testkit import build_settings
from fastapi.testclient import TestClient

from llm_gateway.core.config import Settings
from llm_gateway.main import create_app

SECRET = "s3cr3t"
COMPLETION_BODY = {"messages": [{"role": "user", "content": "hi"}]}

#: 需要内部鉴权的模型出口(路径, 请求体)
ROUTES = [
    ("/v1/completions", COMPLETION_BODY),
    ("/v1/completions/stream", COMPLETION_BODY),
    ("/v1/embeddings", {"input": ["hi"]}),
]


def _settings(**overrides) -> Settings:
    values = {"internal_secret": SECRET, "byok_enabled": False}
    values.update(overrides)
    return build_settings(Settings, **values)


@pytest.fixture(name="secure_client")
def _secure_client():
    with TestClient(create_app(_settings())) as client:
        yield client


def _stub_litellm(monkeypatch) -> list[dict]:
    """打桩三个上游入口并返回调用记录;断言未授权请求不产生调用。"""
    calls: list[dict] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):

            async def chunks():
                delta = type("D", (), {"content": "ok", "tool_calls": None})()
                yield type(
                    "Chunk",
                    (),
                    {
                        "choices": [type("C", (), {"delta": delta})()],
                        "model": "deepseek/deepseek-chat",
                    },
                )()

            return chunks()
        message = type("M", (), {"content": "ok", "tool_calls": None})()
        return type(
            "R",
            (),
            {
                "choices": [type("C", (), {"message": message})()],
                "usage": None,
                "model": "deepseek/deepseek-chat",
            },
        )()

    async def fake_aembedding(**kwargs):
        calls.append(kwargs)
        return {"data": [{"embedding": [0.1]}], "model": "bge-m3", "usage": None}

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "aembedding", fake_aembedding)
    return calls


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_missing_secret_is_rejected(secure_client, monkeypatch, path, body) -> None:
    calls = _stub_litellm(monkeypatch)
    resp = secure_client.post(path, json=body)

    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.internal_forbidden"
    assert resp.json()["message"] == "内部调用未开放"
    assert calls == []  # 鉴权先于 BYOK 与上游调用


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_wrong_secret_is_rejected(secure_client, path, body) -> None:
    resp = secure_client.post(path, json=body, headers={"X-Internal-Secret": "nope"})

    assert resp.status_code == 401
    assert resp.json()["code"] == "auth.internal_forbidden"


def test_almost_matching_secret_is_rejected(secure_client) -> None:
    """只差最后一个字符也必须拒绝(常量时间比较不给"接近"留口子)。"""
    resp = secure_client.post(
        "/v1/completions",
        json=COMPLETION_BODY,
        headers={"X-Internal-Secret": SECRET[:-1] + "X"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_correct_secret_allows_model_routes(
    secure_client, monkeypatch, path, body
) -> None:
    calls = _stub_litellm(monkeypatch)
    resp = secure_client.post(path, json=body, headers={"X-Internal-Secret": SECRET})

    assert resp.status_code == 200
    assert len(calls) == 1


def test_models_endpoint_is_exempt(secure_client) -> None:
    """模型列表只是元数据,不烧 token,免鉴权(前端/内部探活要用)。"""
    resp = secure_client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["default_model"]


def test_health_endpoints_are_exempt(secure_client) -> None:
    assert secure_client.get("/healthz").status_code == 200
    assert secure_client.get("/readyz").status_code == 200


def test_open_mode_without_secret_allows_any_caller(client, monkeypatch) -> None:
    """INTERNAL_SECRET 留空 = 本地 dev 放行(生产必须配,README 有警示)。"""
    calls = _stub_litellm(monkeypatch)
    resp = client.post("/v1/completions", json=COMPLETION_BODY)

    assert resp.status_code == 200
    assert len(calls) == 1


def test_open_mode_ignores_client_supplied_secret(client, monkeypatch) -> None:
    calls = _stub_litellm(monkeypatch)
    resp = client.post(
        "/v1/completions",
        json=COMPLETION_BODY,
        headers={"X-Internal-Secret": "whatever"},
    )

    assert resp.status_code == 200
    assert len(calls) == 1
