"""重排:提供方装配、网关客户端、检索服务层降级、API 三态。

重排是增益不是依赖——网关挂掉必须退回 RRF 顺序(200 + rerank="skipped"),
这条降级链路是本节最重要的断言。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from common.errors import UpstreamError
from fastapi.testclient import TestClient

from knowledge.main import create_app
from knowledge.services import knowledge as knowledge_service
from knowledge.services import search as search_service
from knowledge.services.rerank import (
    GATEWAY_PROVIDER,
    GatewayReranker,
    _scores,
    build_reranker,
)
from tests.conftest import ACCOUNT, _ingest, _settings, verifier

CONTRACT = "本周与客户 A 的合同评审安排在周四下午,需要准备报价单。"
BUDGET = "财务预算上限说明:本季度市场费用不超过 50 万,超支需 VP 审批。"
PRODUCT = "产品路线图:Q3 上线知识库混合检索,顺带修复导出问题。"


def _chunk(chunk_id: str, ordinal: int, content: str = "正文"):
    return SimpleNamespace(
        id=chunk_id, ordinal=ordinal, content=content, document_id=f"doc-{chunk_id}"
    )


def _hits(*items: tuple) -> list[search_service.Hit]:
    """(id, ordinal[, 正文]) → Hit;正文省略时用 id 生成,只关心顺序的用例不必写。"""
    hits = []
    for chunk_id, ordinal, *rest in items:
        content = rest[0] if rest else f"正文{chunk_id}"
        hits.append(
            search_service.Hit(
                chunk=_chunk(chunk_id, ordinal, content), title=content, score=0.5
            )
        )
    return hits


class FakeReranker:
    """按调用方的规则打分;记录每次送进来的候选,便于断言池大小与实际重排。"""

    name = GATEWAY_PROVIDER
    model = "dashscope/qwen3-rerank"

    def __init__(self, scores=None, error: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self._scores = scores
        self._error = error

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append(list(documents))
        if self._error is not None:
            raise self._error
        if self._scores is not None:
            return self._scores(documents)
        return [float(len(documents) - index) for index in range(len(documents))]


def _patch_search(monkeypatch, hits, captured: dict) -> None:
    async def fake_search(session, account_id, query, limit, embedder, **kwargs):
        captured["limit"] = limit
        captured["kwargs"] = kwargs
        return hits[:limit], "hybrid"

    monkeypatch.setattr(search_service, "search", fake_search)


# ---- 服务层:重排生效与三种降级 ----


async def test_search_reranks_and_truncates_within_pool(monkeypatch) -> None:
    captured: dict = {}
    hits = _hits(("a", 0, "甲"), ("b", 1, "乙"), ("c", 2, "丙"))
    _patch_search(monkeypatch, hits, captured)
    reranker = FakeReranker(scores=lambda docs: [0.2, 0.95, 0.5])

    result, mode, status = await knowledge_service.search(
        None,
        "t-1",
        "q",
        2,
        embedder=None,
        reranker=reranker,
        rerank_candidates=3,
    )

    assert status == "applied"
    assert mode == "hybrid"
    assert captured["limit"] == 3  # 粗排池取 max(limit, rerank_candidates)
    assert [hit.chunk.id for hit in result] == ["b", "c"]  # 按重排分数降序截 limit
    assert [hit.score for hit in result] == [0.95, 0.5]
    assert reranker.calls == [["甲", "乙", "丙"]]


async def test_search_requires_bigger_pool_only_when_reranking(monkeypatch) -> None:
    captured: dict = {}
    _patch_search(monkeypatch, _hits(("a", 0)), captured)

    result, _, status = await knowledge_service.search(
        None, "t-1", "q", 2, embedder=None, reranker=None
    )

    assert status is None
    assert captured["limit"] == 2  # 不重排就不放大候选
    assert len(result) == 1


async def test_search_single_hit_skips_rerank(monkeypatch) -> None:
    captured: dict = {}
    _patch_search(monkeypatch, _hits(("a", 0)), captured)
    reranker = FakeReranker()

    result, _, status = await knowledge_service.search(
        None, "t-1", "q", 5, embedder=None, reranker=reranker, rerank_candidates=20
    )

    assert status == "skipped"
    assert reranker.calls == []  # 一条候选没有信息量,不该多打一次外部请求
    assert [hit.chunk.id for hit in result] == ["a"]


async def test_search_gateway_failure_falls_back_to_rrf_order(monkeypatch) -> None:
    captured: dict = {}
    hits = _hits(("a", 0), ("b", 1), ("c", 2))
    _patch_search(monkeypatch, hits, captured)
    reranker = FakeReranker(
        error=UpstreamError("knowledge.rerank_unavailable", "模型网关不可达:boom")
    )

    result, _, status = await knowledge_service.search(
        None, "t-1", "q", 2, embedder=None, reranker=reranker, rerank_candidates=3
    )

    assert status == "skipped"
    assert [hit.chunk.id for hit in result] == ["a", "b"]  # 原 RRF 顺序,未 500
    assert [hit.score for hit in result] == [0.5, 0.5]


async def test_search_score_mismatch_falls_back(monkeypatch) -> None:
    captured: dict = {}
    _patch_search(monkeypatch, _hits(("a", 0), ("b", 1), ("c", 2)), captured)
    reranker = FakeReranker(scores=lambda docs: [0.9, 0.1])  # 少了一条

    result, _, status = await knowledge_service.search(
        None, "t-1", "q", 3, embedder=None, reranker=reranker, rerank_candidates=3
    )

    assert status == "skipped"
    assert [hit.chunk.id for hit in result] == ["a", "b", "c"]


# ---- API:默认关闭、生效、降级 ----


def _client(tmp_path, reranker) -> TestClient:
    return TestClient(
        create_app(_settings(tmp_path), reranker=reranker, token_verifier=verifier())
    )


def test_search_api_defaults_to_no_rerank(client: TestClient) -> None:
    _ingest(client, "预算", BUDGET)
    resp = client.post(
        "/v1/knowledge/search", json={"query": "预算审批"}, headers=ACCOUNT
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rerank"] is None  # 默认 rerank_provider="none"
    assert body["rerank_model"] is None
    assert body["hits"]


def test_search_api_applies_rerank(tmp_path) -> None:
    reranker = FakeReranker(
        scores=lambda docs: [1.0 if "预算" in document else 0.0 for document in docs]
    )
    with _client(tmp_path, reranker) as client:
        _ingest(client, "合同", CONTRACT)
        _ingest(client, "预算", BUDGET)
        _ingest(client, "路线图", PRODUCT)
        resp = client.post(
            "/v1/knowledge/search",
            json={"query": "预算审批", "limit": 3},
            headers=ACCOUNT,
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rerank"] == "applied"
    assert body["rerank_model"] == reranker.model
    assert len(body["hits"]) >= 2
    assert body["hits"][0]["content"] == BUDGET  # 含"预算"的块被顶到第一
    assert body["hits"][0]["score"] == 1.0  # 分数改为重排相关性分数


def test_search_api_degrades_when_gateway_down(tmp_path) -> None:
    reranker = FakeReranker(
        error=UpstreamError("knowledge.rerank_unavailable", "模型网关不可达:boom")
    )
    with _client(tmp_path, reranker) as client:
        _ingest(client, "预算", BUDGET)
        resp = client.post(
            "/v1/knowledge/search", json={"query": "预算审批"}, headers=ACCOUNT
        )

    assert resp.status_code == 200  # 检索不因重排挂掉
    body = resp.json()
    assert body["rerank"] == "skipped"
    assert body["rerank_model"] == reranker.model  # 仍如实回报用了哪套重排
    assert body["hits"]


# ---- 网关客户端 ----


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_gateway_reranker_posts_and_aligns_scores() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["json"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "qwen3-rerank",
                # 故意乱序 + 缺一条:未命中的补 0
                "results": [
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.4},
                ],
            },
        )

    reranker = GatewayReranker(
        "http://gateway.test",
        "dashscope/qwen3-rerank",
        internal_secret="s3cr3t",
        transport=_transport(handler),
    )
    try:
        scores = await reranker.rerank("q", ["甲", "乙", "丙"])
    finally:
        await reranker.aclose()

    assert captured["path"] == "/v1/rerank"
    assert captured["headers"]["x-internal-secret"] == "s3cr3t"
    assert b'"documents"' in captured["json"]
    assert scores == [0.4, 0.0, 0.9]


async def test_gateway_reranker_empty_documents_short_circuits() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("不该发请求")

    reranker = GatewayReranker(
        "http://gateway.test", "m", transport=_transport(handler)
    )
    try:
        assert await reranker.rerank("q", []) == []
    finally:
        await reranker.aclose()


async def test_gateway_reranker_maps_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"code": "llm.rerank_error", "message": "上游炸了"}
        )

    reranker = GatewayReranker(
        "http://gateway.test", "m", transport=_transport(handler)
    )
    try:
        with pytest.raises(UpstreamError) as info:
            await reranker.rerank("q", ["甲"])
    finally:
        await reranker.aclose()

    assert info.value.code == "llm.rerank_error"
    assert "上游炸了" in info.value.message


async def test_gateway_reranker_maps_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reranker = GatewayReranker(
        "http://gateway.test", "m", transport=_transport(handler)
    )
    try:
        with pytest.raises(UpstreamError) as info:
            await reranker.rerank("q", ["甲"])
    finally:
        await reranker.aclose()

    assert info.value.code == "knowledge.rerank_unavailable"


async def test_gateway_reranker_rejects_bad_results() -> None:
    payloads = [
        {"results": []},  # 空结果
        {"results": [{"index": 9, "relevance_score": 0.9}]},  # 越界下标
    ]
    for payload in payloads:

        def handler(request: httpx.Request, body=payload) -> httpx.Response:
            return httpx.Response(200, json=body)

        reranker = GatewayReranker(
            "http://gateway.test", "m", transport=_transport(handler)
        )
        try:
            with pytest.raises(UpstreamError) as info:
                await reranker.rerank("q", ["甲"])
        finally:
            await reranker.aclose()
        assert info.value.code == "knowledge.rerank_mismatch"


async def test_gateway_reranker_rejects_malformed_payloads() -> None:
    """200 但非 JSON / 分数非数值:都是契约破坏,统一降级而不是 500。"""
    responses = [
        httpx.Response(200, text="<html>oops</html>"),
        httpx.Response(200, json={"results": [{"index": 0, "relevance_score": "高"}]}),
    ]
    for response in responses:

        def handler(request: httpx.Request, body=response) -> httpx.Response:
            return body

        reranker = GatewayReranker(
            "http://gateway.test", "m", transport=_transport(handler)
        )
        try:
            with pytest.raises(UpstreamError) as info:
                await reranker.rerank("q", ["甲"])
        finally:
            await reranker.aclose()
        assert info.value.code == "knowledge.rerank_mismatch"


def test_scores_fills_missing_with_zero() -> None:
    body = {"results": [{"index": 1, "relevance_score": 0.7}]}
    assert _scores(body, 3) == [0.0, 0.7, 0.0]


# ---- 装配 ----


def test_build_reranker_providers() -> None:
    assert build_reranker("none", model="m", gateway_url="http://g") is None

    gateway = build_reranker(
        "gateway",
        model="dashscope/qwen3-rerank",
        gateway_url="http://g",
        timeout_seconds=5,
        internal_secret="s",
    )
    assert isinstance(gateway, GatewayReranker)
    assert gateway.model == "dashscope/qwen3-rerank"
    assert gateway.internal_secret == "s"

    with pytest.raises(ValueError):
        build_reranker("cohere", model="m", gateway_url="http://g")


async def test_build_reranker_gateway_closes() -> None:
    gateway = build_reranker("gateway", model="m", gateway_url="http://g")
    assert gateway is not None
    await gateway.aclose()  # type: ignore[attr-defined]
