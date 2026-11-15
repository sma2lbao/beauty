"""R9 检索质量回归:BM25 词面分与 MMR 重排的固定评测集。

语料与查询见 agent_orchestrator.evals.retrieval_quality;这里只断言"不退化"
的硬指标,避免把逐条排名写死导致语料一调就整片失败。
"""

from __future__ import annotations

import math

from agent_orchestrator.evals.retrieval_quality import (
    GOLDEN_DOCS,
    GOLDEN_QUERIES,
    MMR_LAMBDA,
    TOP_K,
    run_golden_suite,
)
from agent_orchestrator.models.memory import Memory
from agent_orchestrator.services.memory import (
    _mmr_order,
    _token_counters,
    _tokenize,
    bm25_lexical_scores,
)


def test_golden_suite_shape() -> None:
    report = run_golden_suite()
    assert report["top_k"] == TOP_K
    assert report["mmr_lambda"] == MMR_LAMBDA
    assert set(report["report"]) == {"legacy", "bm25", "bm25_mmr"}
    for variant in report["report"].values():
        assert variant["queries"] == len(GOLDEN_QUERIES)
        assert len(variant["details"]) == len(GOLDEN_QUERIES)


def test_bm25_beats_legacy_on_golden_queries() -> None:
    report = run_golden_suite()["report"]
    legacy = report["legacy"]
    bm25 = report["bm25"]
    # 词面口径改造不允许掉召回;"用户 部署"这类常见词/罕见词同场竞争,
    # 旧集合余弦给两者同权,BM25 按 IDF 把罕见词命中排到前面。
    assert bm25["recall_at_k"] >= legacy["recall_at_k"]
    assert bm25["mrr"] > legacy["mrr"]
    assert bm25["mrr"] >= 0.9


def test_mmr_reduces_redundancy_without_losing_recall() -> None:
    report = run_golden_suite()["report"]
    bm25 = report["bm25"]
    mmr = report["bm25_mmr"]
    # MMR 只重排不增删:召回不变,但前两条更少重复(注入按顺序裁剪预算)。
    assert mmr["recall_at_k"] == bm25["recall_at_k"]
    assert mmr["redundancy"] < bm25["redundancy"]


def test_mmr_lambda_one_keeps_bm25_order() -> None:
    memories = [
        Memory(id=f"m{index}", account_id="eval", content=doc.content)
        for index, doc in enumerate(GOLDEN_DOCS)
    ]
    tokens = set(_tokenize("周报 图表 表格 呈现"))
    counters = _token_counters(memories)
    lexical = bm25_lexical_scores(tokens, memories, counters=counters)
    scored = sorted(
        ((lexical.get(memory.id, 0.0), 0.0, memory) for memory in memories),
        key=lambda item: (-item[1], item[2].id),
    )
    order = _mmr_order(scored, mmr_lambda=1.0, counters=counters)
    assert [memory.id for _score, memory in order] == [
        memory.id for _relevance, _score, memory in scored
    ]


def test_bm25_ignores_query_terms_absent_from_corpus() -> None:
    """查询里的新造词不该计入归一化分母,否则会整体压低相关文档的分数。"""
    memories = [
        Memory(id="m1", account_id="eval", content="项目代号是蓝鲸"),
        Memory(id="m2", account_id="eval", content="部署环境是首尔"),
    ]
    counters = _token_counters(memories)
    tokens = set(_tokenize("蓝鲸 完全不存在的词"))
    scores = bm25_lexical_scores(tokens, memories, counters=counters)
    assert scores["m1"] > 0.9
    assert math.isclose(scores["m2"], 0.0)
