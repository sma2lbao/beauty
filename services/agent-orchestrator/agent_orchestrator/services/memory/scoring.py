"""记忆打分与文本工具:BM25 词面分、余弦相似度、加法式打分、MMR 重排、敏感信息过滤。"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.services.embeddings import (
    local_embedding,
)
from agent_orchestrator.services.embeddings import (
    tokenize as _tokenize,
)

from .constants import (
    _CREDIT_CARD_RE,
    _EMBEDDING_WEIGHT,
    _LEXICAL_WEIGHT,
    _PINNED_BONUS,
    _RECENCY_BONUS,
    _SECRET_PATTERNS,
    _SNIPPET_CHARS,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """SQLite 取回的时间是 naive(无时区),统一按 UTC 解释再做时间运算。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def embed_text(text: str) -> list[float]:
    """把文本映射成确定性 256 维单位向量(委托本地哈希实现,零网络)。

    同步纯函数保留给写入与测试直接调用;需要语义能力时走 :func:`embed_one`
    (可插拔 provider,失败自动回退到这里)。空文本返回零向量。
    """
    return local_embedding(text)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """向量余弦;双方都是单位向量时即点积,维度不等时按较短者截断。"""
    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    return sum(left[index] * right[index] for index in range(size))


def _lexical_overlap(query_tokens: set[str], content: str) -> float:
    """旧词面分:集合余弦,范围 0~1(1 = 词元集合一致)。

    保留为 _score_memory 的单条直调口径与评测基线;检索主路径已改用
    :func:`bm25_lexical_scores`(IDF 降低常见词权重、长度归一抑制长文偏置)。
    """
    document_tokens = set(_tokenize(content))
    if not query_tokens or not document_tokens:
        return 0.0
    intersection = len(query_tokens & document_tokens)
    if intersection == 0:
        return 0.0
    return intersection / math.sqrt(len(query_tokens) * len(document_tokens))


_BM25_K1 = 1.2
_BM25_B = 0.75


def _token_counters(memories: Sequence[Memory]) -> dict[str, Counter[str]]:
    """一次性算出候选集合的词频表:检索的 BM25 与 MMR 共用,避免重复切词。"""
    return {memory.id: Counter(_tokenize(memory.content)) for memory in memories}


def bm25_lexical_scores(
    query_tokens: Sequence[str] | set[str],
    memories: Sequence[Memory],
    counters: dict[str, Counter[str]] | None = None,
) -> dict[str, float]:
    """BM25 词面分并按查询侧 IDF 权重归一到 0~1(1 = 查询词元全部命中且词频饱和)。

    IDF 与平均文档长度都在**当前候选集内**统计:记忆检索的候选集是账号有效
    记忆(上限 200 条),局部统计比全局语料更稳定,也不需要额外维护索引。
    归一化让词面分量与向量分量可以继续按 0.65/0.35 融合,阈值语义不变;
    分母只含候选集里出现过的查询词元(语料中不存在的词无人可命中)。
    """
    tokens = list(dict.fromkeys(query_tokens))
    if not tokens or not memories:
        return {}
    term_counts = counters if counters is not None else _token_counters(memories)
    document_frequency: Counter[str] = Counter()
    lengths: dict[str, int] = {}
    for memory in memories:
        counts = term_counts[memory.id]
        lengths[memory.id] = sum(counts.values())
        document_frequency.update(counts.keys())
    total_terms = sum(lengths.values())
    average_length = total_terms / len(memories) if memories else 0.0
    size = len(memories)
    weights = {
        token: math.log(
            1.0
            + (size - document_frequency.get(token, 0) + 0.5)
            / (document_frequency.get(token, 0) + 0.5)
        )
        for token in tokens
    }
    # 只按"候选集内出现过"的查询词元归一:查询里可能有语料中根本不存在的词
    # (新造词、错别字),它们无人可命中,计入分母只会整体压低分数、把相关
    # 文档挤到阈值之下;真正该惩罚的是"候选集里存在却没匹配上"的词。
    normalizer = (
        sum(weight for token, weight in weights.items() if document_frequency[token])
        or 1.0
    )
    scores: dict[str, float] = {}
    for memory in memories:
        counts = term_counts[memory.id]
        length_ratio = (
            1.0 - _BM25_B + _BM25_B * (lengths[memory.id] / average_length)
            if average_length > 0
            else 1.0
        )
        score = 0.0
        for token in tokens:
            frequency = counts.get(token, 0)
            if frequency:
                score += (
                    weights[token]
                    * (frequency * (_BM25_K1 + 1.0))
                    / (frequency + _BM25_K1 * length_ratio)
                )
        # 词频饱和后单条可能略超 1(短文档),夹紧以保持分量口径稳定。
        scores[memory.id] = min(1.0, score / normalizer)
    return scores


def _token_similarity(left: set[str], right: set[str]) -> float:
    """词元集合余弦:MMR 的重叠度,1 = 完全重复。"""
    if not left or not right:
        return 0.0
    return len(left & right) / math.sqrt(len(left) * len(right))


def _score_memory(
    query_tokens: set[str],
    query_embedding: Sequence[float],
    memory: Memory,
    now: datetime,
    *,
    lexical: float | None = None,
) -> tuple[float, float]:
    """返回 (相关性, 排序分):相关性用于门槛过滤,排序分含 pinned/新近加成。

    lexical 由检索路径预计算(BM25,候选集内归一);单独调用时退回旧词面口径,
    便于断言单条打分而不必构造整个候选集。

    向量分量只在两侧维度一致时计入:换过嵌入模型后历史行与新查询的向量
    来自不同模型,截断算出的余弦看似合理却不可比,宁可只保留词面分量。
    """
    lexical_score = (
        lexical
        if lexical is not None
        else _lexical_overlap(query_tokens, memory.content)
    )
    stored_embedding = memory.embedding or []
    if stored_embedding and len(stored_embedding) == len(query_embedding):
        embedding = (_cosine(query_embedding, stored_embedding) + 1.0) / 2.0
    else:
        embedding = 0.0
    relevance = _LEXICAL_WEIGHT * lexical_score + _EMBEDDING_WEIGHT * embedding
    score = relevance
    if memory.pinned:
        score += _PINNED_BONUS
    last_seen = memory.last_accessed_at or memory.updated_at
    if last_seen is not None:
        age_days = max(0.0, (now - _as_utc(last_seen)).total_seconds() / 86400.0)
        score += _RECENCY_BONUS * (0.5 ** (age_days / 30.0))
    return relevance, score


def _mmr_order(
    scored: Sequence[tuple[float, float, Memory]],
    *,
    mmr_lambda: float,
    counters: dict[str, Counter[str]],
) -> list[tuple[float, Memory]]:
    """按 MMR 贪心重排:lambda 越大越偏相关性,1.0 等价于纯相关性排序。

    scored 需已按 (排序分降序, id 升序) 排好;重排只调整顺序、不增删条目,
    字符预算与条数上限仍由调用方在重排后统一裁剪。
    """
    ordered: list[tuple[float, Memory]] = [
        (score, memory) for _relevance, score, memory in scored
    ]
    if mmr_lambda >= 1.0 or len(ordered) <= 1:
        return ordered
    remaining = list(ordered)
    selected: list[tuple[float, Memory]] = []
    token_sets = {memory_id: set(counts) for memory_id, counts in counters.items()}
    while remaining:
        best_index = 0
        best_value = float("-inf")
        for index, (score, memory) in enumerate(remaining):
            redundancy = max(
                (
                    _token_similarity(
                        token_sets.get(memory.id, set()),
                        token_sets.get(picked.id, set()),
                    )
                    for _picked_score, picked in selected
                ),
                default=0.0,
            )
            value = mmr_lambda * score - (1.0 - mmr_lambda) * redundancy
            if value > best_value:
                best_index = index
                best_value = value
        selected.append(remaining.pop(best_index))
    return selected


def _snippet(content: str) -> str:
    """把内容压成单行短句:召回块是 prompt 的一部分,不能带大段换行。"""
    text = " ".join(content.split())
    if len(text) <= _SNIPPET_CHARS:
        return text
    return f"{text[:_SNIPPET_CHARS]}…"


def _luhn_valid(digits: str) -> bool:
    """银行卡号校验:避免把订单号/长数字误判成敏感信息。"""
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def find_sensitive(text: str) -> str | None:
    """识别疑似敏感凭据;命中返回类别,未命中返回 None。

    确定性规则优先(隐私过滤不能依赖模型),银行卡再做 Luhn 校验。
    """
    candidate = text or ""
    for category, pattern in _SECRET_PATTERNS:
        if pattern.search(candidate):
            return category
    for match in _CREDIT_CARD_RE.finditer(candidate):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return "credit_card"
    return None


def content_similarity(left: str, right: str) -> float:
    """词面相似度:交集 / 较短集合大小(与抽取去重同一口径)。"""
    left_tokens = set(_tokenize(left))
    right_tokens = set(_tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _render_rows(title: str, rows: Sequence[Memory], max_chars: int) -> str:
    """把记忆行渲染成受预算约束的提示块;一行都放不下时返回空串。"""
    if max_chars <= 0 or not rows:
        return ""
    lines = [title]
    used = len(title)
    for memory in rows:
        line = f"- [{memory.kind}] {_snippet(memory.content)}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
