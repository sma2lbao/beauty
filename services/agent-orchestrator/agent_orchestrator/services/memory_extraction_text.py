"""记忆抽取的词面原语:空白归一口径与本地确定性相似度(不依赖 embedding)。

从 services/memory_extraction.py 拆出(门面仍由 memory_extraction.py 全量重导出):
只放纯函数,抽取与巩固共用同一套切词/相似度语义,保证"去重"与"巩固"判断一致。
"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _normalize(text: str) -> str:
    """折叠空白成单行:LLM 输出里的换行/缩进不影响去重与入库。"""
    return " ".join((text or "").split())


def _tokens(text: str) -> set[str]:
    """词面 token 集合:拉丁词整体保留,中文按相邻 bigram(单字段保留单字)。

    与 memory_service 的切词思路一致,但这里独立实现:抽取/巩固的相似度语义
    不应随检索打分策略变化(检索可以调权重,去重只关心"是不是同一句话")。
    """
    lowered = _normalize(text).lower()
    tokens = {f"w:{word}" for word in _WORD_RE.findall(lowered)}
    for run in _CJK_RE.findall(lowered):
        if len(run) == 1:
            tokens.add(f"c:{run}")
            continue
        tokens.update(f"c:{run[index : index + 2]}" for index in range(len(run) - 1))
    return tokens


def _similarity(left: set[str], right: set[str]) -> float:
    """词面相似度:交集 / 较短集合大小(包含度)。

    用包含度而不是 Jaccard:抽取出来的一句话常是既有记忆的细化或片段,严格
    Jaccard 会漏掉这类近重复;包含度对"短句被长句覆盖"更敏感,正好符合去重目标。
    """
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))
