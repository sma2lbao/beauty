"""关键词侧打分:混合检索的"字面命中"一路(不依赖分词扩展)。

为什么不用 Postgres 的 `tsvector('simple')`:它按空格/标点切词,中文整段会被当成
**一个 token**,查询串几乎永远匹配不上;装 `zhparser`/`pg_jieba` 又给生产库加了一个
扩展依赖。这里用"拉丁词整体 + 中文二元组"匹配,既能命中型号、合同号这类专有名词,
也不增加数据库侧依赖。

候选粗筛由 Postgres 的 `pg_trgm` 索引(`ILIKE`)负责,打分仍在进程内做,
所以 SQLite(测试/离线)与 Postgres 的排序结果一致、可复现。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-z0-9]+")
#: 一条查询最多取多少个词参与匹配:查询越长,单字噪声越重,收益也越低。
_MAX_TERMS = 32


def lexical_terms(text: str) -> list[str]:
    """把查询串切成匹配单元:中文按二元组(单字回退),拉丁词/数字整体。"""
    lowered = text.lower()
    terms: list[str] = []
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            terms.append(run)
        else:
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
    terms.extend(_WORD.findall(lowered))
    return _dedupe(terms)[:_MAX_TERMS]


def _dedupe(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            ordered.append(term)
    return ordered


def match_terms(text: str) -> list[str]:
    """与 `lexical_terms` 同口径的文本侧切分(用于跨语言/纯中文文档的兜底匹配)。"""
    return lexical_terms(text)


def lexical_score(terms: list[str], content: str) -> float:
    """词法相关度(0~1):命中覆盖率为主,词频只做小幅加权。

    覆盖率 = 命中的词数 / 查询词数——它决定"这条分块到底讲没讲这件事";
    词频只在覆盖相同时区分"提到一次"与"通篇在讲"。
    """
    if not terms or not content:
        return 0.0
    lowered = content.lower()
    matched = 0
    occurrences = 0
    for term in terms:
        count = lowered.count(term)
        if count:
            matched += 1
            occurrences += count
    if matched == 0:
        return 0.0
    coverage = matched / len(terms)
    density = min(occurrences / len(terms), 1.0)
    return round(0.9 * coverage + 0.1 * density, 6)
