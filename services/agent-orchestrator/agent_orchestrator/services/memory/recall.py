"""记忆检索与注入块:相关性检索、召回块、程序性约定块、核心记忆块。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.repositories import MemoryRepository
from agent_orchestrator.services.embeddings import embed_one
from agent_orchestrator.services.embeddings import tokenize as _tokenize

from .constants import (
    _CANDIDATE_LIMIT,
    _CONVENTION_LIMIT,
    _CORE_LIMIT,
    _MIN_RELEVANCE,
    _PROCEDURE_KIND,
    CONVENTION_PREFIX,
    CORE_PREFIX,
    RECALL_PREFIX,
)
from .scoring import (
    _mmr_order,
    _render_rows,
    _score_memory,
    _snippet,
    _token_counters,
    _utcnow,
    bm25_lexical_scores,
)


async def search_memories(
    session: AsyncSession,
    account_id: str,
    query: str,
    *,
    workspace_id: str | None = None,
    kind: str | None = None,
    limit: int = 5,
    max_chars: int = 2000,
    touch: bool = False,
    exclude_ids: set[str] | None = None,
    mmr_lambda: float = 0.7,
) -> list[Memory]:
    """按相关性检索有效记忆;touch=True 时命中后累加访问统计。

    只读查询(GET /memories/search、管理面预览)默认不写库;只有真正把结果
    注入上下文或交给模型工具时才 touch=True。先按字符预算挑选(避免召回块
    超长),再统一记一次访问统计;只 flush 不 commit,事务边界由调用方负责。

    排序分(P3-C):BM25 词面分(候选集内 IDF/长度归一到 0~1)与向量余弦按
    0.65/0.35 融合,再叠加 pinned/新近小加成;最后按 MMR 重排(mm_lambda
    取 1.0 时关闭),避免近重复条目占满召回预算。
    """
    text = (query or "").strip()
    if not text or limit <= 0:
        return []
    repository = MemoryRepository(session)
    candidates = await repository.list_active(
        account_id,
        workspace_id=workspace_id,
        kind=kind,
        # 没有工作区上下文时只看账号级记忆,不跨工作区泄漏 workspace 级内容
        scope=None if workspace_id else "account",
        limit=_CANDIDATE_LIMIT,
    )
    if exclude_ids:
        candidates = [item for item in candidates if item.id not in exclude_ids]
    if not candidates:
        return []

    now = _utcnow()
    query_tokens = set(_tokenize(text))
    # 查询向量与写入向量走同一个 provider;provider 不可用时自动回退本地哈希。
    query_embedding, _ = await embed_one(text)
    counters = _token_counters(candidates)
    lexical_scores = bm25_lexical_scores(query_tokens, candidates, counters=counters)
    scored: list[tuple[float, float, Memory]] = []
    for memory in candidates:
        relevance, score = _score_memory(
            query_tokens,
            query_embedding,
            memory,
            now,
            lexical=lexical_scores.get(memory.id, 0.0),
        )
        if relevance < _MIN_RELEVANCE:
            continue
        scored.append((relevance, score, memory))
    scored.sort(key=lambda item: (-item[1], item[2].id))

    hits: list[Memory] = []
    used_chars = 0
    for _score, memory in _mmr_order(scored, mmr_lambda=mmr_lambda, counters=counters):
        if len(hits) >= limit:
            break
        cost = len(memory.content) + 16
        # 第一条无论多长都要给(否则小 max_chars 会让检索永远为空)。
        if hits and used_chars + cost > max_chars:
            continue
        hits.append(memory)
        used_chars += cost

    if touch and hits:
        for memory in hits:
            memory.access_count += 1
            memory.last_accessed_at = now
        await session.flush()
    return hits


async def recall_block(
    session: AsyncSession,
    account_id: str,
    query: str,
    *,
    workspace_id: str | None = None,
    limit: int = 5,
    max_chars: int = 2000,
    exclude_ids: set[str] | None = None,
) -> str:
    """把相关记忆拼成可注入 system prompt 的中文块;无命中返回空串。

    exclude_ids 用于跳过已由核心记忆块常驻注入的条目,避免同一内容出现两次。
    """
    hits = await search_memories(
        session,
        account_id,
        query,
        workspace_id=workspace_id,
        limit=limit,
        max_chars=max_chars,
        touch=True,
        exclude_ids=exclude_ids,
    )
    if not hits:
        return ""
    lines = [RECALL_PREFIX]
    used_chars = len(lines[0])
    for memory in hits:
        line = f"- [{memory.kind}] {_snippet(memory.content)}"
        if used_chars + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used_chars += len(line) + 1
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


async def convention_block(
    session: AsyncSession,
    account_id: str,
    *,
    workspace_id: str | None = None,
    max_chars: int = 2000,
) -> str:
    """把程序性记忆(kind=procedure)拼成**常驻**约定块;无条目返回空串。

    与 recall_block 的区别:约定/流程如果只靠按需召回等于形同虚设——模型
    不知道要去查。所以 procedure 条目每次请求都注入;仍受字符预算约束,
    预算 <=0 时整体关闭(配置 memory_convention_max_chars=0)。
    约定块是上下文不是系统指令:不得据此改变工具权限/审批策略。
    """
    if max_chars <= 0:
        return ""
    rows = await MemoryRepository(session).list_active(
        account_id,
        workspace_id=workspace_id,
        kind=_PROCEDURE_KIND,
        # 没有工作区上下文时只看账号级约定,不注入别的工作区的 workspace 级流程
        scope=None if workspace_id else "account",
        limit=_CONVENTION_LIMIT,
    )
    if not rows:
        return ""
    lines = [CONVENTION_PREFIX]
    used_chars = len(lines[0])
    for memory in rows:
        line = f"- {_snippet(memory.content)}"
        if used_chars + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used_chars += len(line) + 1
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


async def core_memories(
    session: AsyncSession,
    account_id: str,
    *,
    workspace_id: str | None = None,
    limit: int = _CORE_LIMIT,
) -> list[Memory]:
    """取常驻注入的核心记忆(injection_mode=always);按 pinned/更新时间排序。"""
    return await MemoryRepository(session).list_core(
        account_id, workspace_id=workspace_id, limit=limit
    )


async def core_block(
    session: AsyncSession,
    account_id: str,
    *,
    workspace_id: str | None = None,
    max_chars: int = 1200,
) -> str:
    """核心记忆常驻块;max_chars<=0 或没有条目时返回空串。"""
    if max_chars <= 0:
        return ""
    rows = await core_memories(session, account_id, workspace_id=workspace_id)
    return core_block_from(rows, max_chars=max_chars)


def core_block_from(rows: list[Memory], *, max_chars: int = 1200) -> str:
    """把已取出的核心记忆渲染成常驻块(调用方复用同一批行做召回去重)。"""
    if max_chars <= 0:
        return ""
    return _render_rows(CORE_PREFIX, rows, max_chars)
