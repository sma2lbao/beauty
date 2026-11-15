"""R9 检索质量评估:固定 golden 语料 + 查询,对比三代排序口径。

三代口径共用同一份融合权重(0.65 词面/0.35 向量)与门槛,只换词面分量:

- legacy:集合余弦重叠 |q∩d| / sqrt(|q||d|)(P2 口径,作为基线);
- bm25:候选集内 IDF + 长度归一,按查询侧权重和归一到 0~1;
- bm25_mmr:在 bm25 排序后按 MMR 重排(默认 lambda=0.7)。

指标:recall@k(前 k 条里命中相关条目的比例)、MRR(第一个相关条目的
倒数排名)、冗余度(第 1 名与第 2 名的词元相似度,越低说明前两条越多样;
注入按顺序裁剪预算,所以前两条是否重复比整段集合更重要)。
评测只做纯词面排序(不注入向量),并把门槛放宽到"相关度 > 0"只看排序,
这样 BM25 的改动不会被嵌入结果掩盖,也不会被生产阈值掩盖;
零网络、确定性,可直接进 CI 回归。运行方式:
python -m agent_orchestrator.evals.retrieval_quality
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.services.memory import (
    _mmr_order,
    _score_memory,
    _token_counters,
    _token_similarity,
    _tokenize,
    bm25_lexical_scores,
)

#: 评估用的 top-k:与生产的默认召回条数一致。
TOP_K = 3
#: MMR 的默认折中系数(与 Settings.memory_mmr_lambda 默认一致)。
MMR_LAMBDA = 0.7


@dataclass(frozen=True)
class GoldenDoc:
    """语料中的一条记忆(只保留排序关心的字段)。"""

    doc_id: str
    content: str
    kind: str = "note"


@dataclass(frozen=True)
class GoldenQuery:
    """一条查询及其相关条目 id(可以有多个)。"""

    query: str
    relevant: tuple[str, ...]


#: 语料刻意包含近重复(周报表格/周报图表)、短查询与长文,覆盖典型召回场景。
GOLDEN_DOCS: tuple[GoldenDoc, ...] = (
    GoldenDoc("d1", "用户偏好周报用表格呈现,每周五发送", "preference"),
    GoldenDoc("d2", "用户偏好周报用图表呈现,越简洁越好", "preference"),
    GoldenDoc("d3", "项目代号是蓝鲸", "fact"),
    GoldenDoc("d4", "部署环境是首尔轻量服务器", "fact"),
    GoldenDoc("d5", "回答问题时先给结论,再给必要细节", "preference"),
    GoldenDoc("d6", "代码使用 TypeScript strict 模式,禁止 any", "fact"),
    GoldenDoc("d7", "周报需要包含本周完成事项与下周计划", "note"),
    GoldenDoc("d8", "用户不喜欢冗长的开场白和客套话", "preference"),
    GoldenDoc("d9", "生产部署在上海机房", "fact"),
    GoldenDoc("d10", "用户密码策略", "fact"),
)

#: 查询集覆盖:短词命中、中英混合、近重复(检验多样性)、长词精确匹配,
#: 以及"常见词 vs 罕见词同场竞争"(旧集合余弦给两者同权,BM25 按 IDF 区分)。
GOLDEN_QUERIES: tuple[GoldenQuery, ...] = (
    GoldenQuery("周报 表格", ("d1",)),
    GoldenQuery("周报 图表 表格 呈现", ("d1", "d2")),
    GoldenQuery("周报 表格 计划 事项", ("d1", "d7")),
    GoldenQuery("回答 结论 细节 开场白 客套", ("d5", "d8")),
    GoldenQuery("项目代号 蓝鲸", ("d3",)),
    GoldenQuery("部署环境", ("d4",)),
    GoldenQuery("TypeScript strict", ("d6",)),
    GoldenQuery("用户 部署", ("d9",)),
    GoldenQuery("用户 密码", ("d10",)),
)


def _as_memory(doc: GoldenDoc) -> Memory:
    """构造未落库的 Memory 对象:打分函数只读 content/embedding/时间戳。"""
    return Memory(
        id=doc.doc_id,
        account_id="eval",
        content=doc.content,
        kind=doc.kind,
        scope="account",
    )


def _rank_legacy(query: str, docs: Sequence[GoldenDoc]) -> list[str]:
    tokens = set(_tokenize(query))
    now = datetime.now(UTC)
    scored = []
    for doc in docs:
        memory = _as_memory(doc)
        relevance, score = _score_memory(tokens, [], memory, now)
        if relevance > 0.0:
            scored.append((score, doc.doc_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [doc_id for _score, doc_id in scored]


def _bm25_scored(query: str, docs: Sequence[GoldenDoc]):
    tokens = set(_tokenize(query))
    now = datetime.now(UTC)
    memories = [_as_memory(doc) for doc in docs]
    counters = _token_counters(memories)
    lexical = bm25_lexical_scores(tokens, memories, counters=counters)
    scored = []
    for memory in memories:
        relevance, score = _score_memory(
            tokens, [], memory, now, lexical=lexical.get(memory.id, 0.0)
        )
        if relevance > 0.0:
            scored.append((relevance, score, memory))
    scored.sort(key=lambda item: (-item[1], item[2].id))
    return tokens, memories, counters, scored


def _rank_bm25(query: str, docs: Sequence[GoldenDoc]) -> list[str]:
    _tokens, _memories, _counters, scored = _bm25_scored(query, docs)
    return [memory.id for _relevance, _score, memory in scored]


def _rank_bm25_mmr(query: str, docs: Sequence[GoldenDoc]) -> list[str]:
    _tokens, _memories, counters, scored = _bm25_scored(query, docs)
    order = _mmr_order(scored, mmr_lambda=MMR_LAMBDA, counters=counters)
    return [memory.id for _score, memory in order]


#: 被测口径:名字 -> 排序函数(新增口径在这里注册即可进入对比)。
RANKERS = {
    "legacy": _rank_legacy,
    "bm25": _rank_bm25,
    "bm25_mmr": _rank_bm25_mmr,
}


def _recall_at(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float:
    if not relevant:
        return 1.0
    hits = set(ranked[:k])
    return len(hits & set(relevant)) / len(relevant)


def _reciprocal_rank(ranked: Sequence[str], relevant: Sequence[str]) -> float:
    targets = set(relevant)
    for index, doc_id in enumerate(ranked, start=1):
        if doc_id in targets:
            return 1.0 / index
    return 0.0


def _redundancy(ranked: Sequence[str], docs: Sequence[GoldenDoc]) -> float:
    """前两条的词元相似度;不足两条记 0(无可比较的重复)。"""
    by_id = {doc.doc_id: doc for doc in docs}
    top = [doc_id for doc_id in ranked[:2] if doc_id in by_id]
    if len(top) < 2:
        return 0.0
    return _token_similarity(
        set(_tokenize(by_id[top[0]].content)),
        set(_tokenize(by_id[top[1]].content)),
    )


def run_variant(
    name: str,
    queries: Sequence[GoldenQuery] = GOLDEN_QUERIES,
    docs: Sequence[GoldenDoc] = GOLDEN_DOCS,
) -> dict[str, Any]:
    """跑一个排序口径:返回 recall@k / MRR / 冗余度与逐查询明细。"""
    ranker = RANKERS[name]
    details = []
    for case in queries:
        ranked = ranker(case.query, docs)
        details.append(
            {
                "query": case.query,
                "ranked": ranked[:TOP_K],
                "recall": round(_recall_at(ranked, case.relevant, TOP_K), 4),
                "rr": round(_reciprocal_rank(ranked, case.relevant), 4),
                "redundancy": round(_redundancy(ranked, docs), 4),
            }
        )
    count = len(details) or 1
    return {
        "variant": name,
        "queries": len(details),
        "recall_at_k": round(sum(item["recall"] for item in details) / count, 4),
        "mrr": round(sum(item["rr"] for item in details) / count, 4),
        "redundancy": round(sum(item["redundancy"] for item in details) / count, 4),
        "details": details,
    }


def run_golden_suite(
    queries: Sequence[GoldenQuery] = GOLDEN_QUERIES,
    docs: Sequence[GoldenDoc] = GOLDEN_DOCS,
) -> dict[str, Any]:
    """跑全部口径并返回对比报告(键与 RANKERS 一致)。"""
    return {
        "report": {name: run_variant(name, queries, docs) for name in RANKERS},
        "top_k": TOP_K,
        "mmr_lambda": MMR_LAMBDA,
    }


def main() -> None:  # pragma: no cover - 手工运行入口
    """打印对比报告(供人工观察趋势,不进测试断言)。"""
    import json

    print(json.dumps(run_golden_suite(), ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
