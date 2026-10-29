"""对话记忆自动抽取与近重复巩固(P2-1)。

一期只在"用户显式写 / 工具显式写"时落记忆;真实对话里大量稳定偏好与事实散落在
闲聊中,需要在对话推进到一定规模后抽取成结构化记忆。本模块只做三件事:

- 用 LLMClient 从 transcript 抽候选(解析刻意宽容:围栏/前后噪声/数组或对象);
- 去重后逐条走 memory_service.write_memory 的公开签名落库;
- 对既有活跃记忆做近重复合并(周期巩固),pinned 永不删除。

事务边界仍由调用方(session_scope)负责;相似度与聚类是本地确定性实现,不依赖
embedding,也不引入向量库——抽取的输入已经是 LLM 输出,再叠加语义模型只会让
离线测试不可复现。

拆分说明:提示词与解析在 memory_extraction_prompt,词面相似度原语在
memory_extraction_text;本模块保留门面重导出,并承载落库、去重与提交后衔接。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING

import structlog
from common.errors import AppError
from database.session import session_scope
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.models.memory import Memory
from agent_orchestrator.repositories import (
    MemoryRepository,
    MessageRepository,
    RunRepository,
)
from agent_orchestrator.services import memory as memory_service
from agent_orchestrator.services import post_commit
from agent_orchestrator.services.memory_extraction_prompt import (
    EXTRACTION_SYSTEM_PROMPT as EXTRACTION_SYSTEM_PROMPT,
)
from agent_orchestrator.services.memory_extraction_prompt import (
    ExtractedMemory as ExtractedMemory,
)
from agent_orchestrator.services.memory_extraction_prompt import (
    _build_transcript as _build_transcript,
)
from agent_orchestrator.services.memory_extraction_prompt import (
    extract_candidates as extract_candidates,
)
from agent_orchestrator.services.memory_extraction_prompt import (
    parse_candidates as parse_candidates,
)
from agent_orchestrator.services.memory_extraction_text import (
    _normalize as _normalize,
)
from agent_orchestrator.services.memory_extraction_text import (
    _similarity as _similarity,
)
from agent_orchestrator.services.memory_extraction_text import _tokens as _tokens

if TYPE_CHECKING:
    from agent_orchestrator.core.config import Settings
    from agent_orchestrator.models.conversations import ChatMessage
    from agent_orchestrator.models.runs import Run
    from agent_orchestrator.services.llm import LLMClient

logger = structlog.get_logger(__name__)


#: 抽取时比较去重的既有记忆条数上限(近期/常用优先,周期巩固兜底)。
_DEDUPE_SCAN_LIMIT = 500
#: 巩固时最多处理多少条活跃记忆:后台任务要控制单轮成本。
_DEFAULT_CONSOLIDATE_LIMIT = 500


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _keep_priority(memory: Memory) -> tuple[int, int, float, str]:
    """巩固时保留哪条的排序键:pinned 优先 → access_count 高 → 创建更早 → id。

    返回元组可直接 min()/sorted():第一项越小越优先。
    """
    created_at = memory.created_at
    if created_at is None:
        created = 0.0
    else:
        created = (
            created_at.replace(tzinfo=UTC).timestamp()
            if created_at.tzinfo is None
            else created_at.timestamp()
        )
    return (
        0 if memory.pinned else 1,
        -int(memory.access_count or 0),
        created,
        memory.id,
    )


def _workspace_key(memory: Memory) -> str | None:
    """记忆的"工作区域"标识:租户级记忆归 None,工作区级记忆归其 workspace_id。"""
    return memory.workspace_id if memory.scope == "workspace" else None


async def remember_candidates(
    session: AsyncSession,
    tenant_id: str,
    *,
    candidates: Sequence[ExtractedMemory],
    workspace_id: str | None = None,
    source_type: str = "chat",
    source_id: str | None = None,
    max_content_chars: int = 8000,
    max_entries: int = 2000,
    dedupe_threshold: float = 0.9,
) -> list[Memory]:
    """把候选写入长期记忆;与同租户/同工作区/同 kind 的活跃记忆近重复时跳过。

    去重只用词面相似,不调 embedding:这里比的是"是不是同一句话",而不是检索
    相关性;写入本身仍由 write_memory 统一做校验/限额/嵌入。
    """
    if not candidates:
        return []
    repository = MemoryRepository(session)
    existing = await repository.list_active(tenant_id, limit=max(_DEDUPE_SCAN_LIMIT, 1))
    known: dict[tuple[str, str | None], list[set[str]]] = {}
    for memory in existing:
        known.setdefault((memory.kind, _workspace_key(memory)), []).append(
            _tokens(memory.content)
        )

    written: list[Memory] = []
    for candidate in candidates:
        kind = (
            candidate.kind if candidate.kind in memory_service.MEMORY_KINDS else "note"
        )
        scope = (
            candidate.scope
            if candidate.scope in memory_service.MEMORY_SCOPES
            else "tenant"
        )
        target_workspace = workspace_id if scope == "workspace" else None
        key = (kind, target_workspace)
        tokens = _tokens(candidate.content)
        if any(
            _similarity(tokens, other) >= dedupe_threshold
            for other in known.get(key, ())
        ):
            continue
        try:
            memory = await memory_service.write_memory(
                session,
                tenant_id,
                content=candidate.content,
                kind=kind,
                scope=scope,
                workspace_id=target_workspace,
                source_type=source_type,
                source_id=source_id,
                max_content_chars=max_content_chars,
                max_entries=max_entries,
            )
        except AppError as exc:
            # 单条候选非法(如 workspace 级缺 workspace_id)或额度满:跳过,
            # 不拖垮整批抽取;额度满时后续候选必然也失败,提前收工。
            logger.warning(
                "memory candidate skipped",
                code=exc.code,
                message=str(exc),
            )
            if exc.code == "memory.limit_reached":
                break
            continue
        known.setdefault(key, []).append(tokens)
        written.append(memory)
    return written


async def extract_from_messages(
    session: AsyncSession,
    llm: LLMClient,
    *,
    tenant_id: str,
    messages: Sequence[ChatMessage],
    workspace_id: str | None = None,
    source_type: str = "chat",
    source_id: str | None = None,
    settings: Settings,
) -> list[Memory]:
    """对话消息 → LLM 抽取 → 去重落库;任一开关关闭直接返回空列表。

    这里不判断"累计多少条"的节流(交给调用方:它有 MessageRepository 计数与
    run/对话生命周期上下文);本函数只负责单次抽取的完整性与幂等去重。
    """
    if not getattr(settings, "memory_enabled", True):
        return []
    if not getattr(settings, "memory_auto_extract_enabled", False):
        return []
    if not messages:
        return []
    max_chars = int(getattr(settings, "memory_extraction_max_chars", 1200) or 0)
    max_items = int(getattr(settings, "memory_extraction_max_items", 5) or 0)
    transcript = _build_transcript(messages, max_chars=max_chars)
    if not transcript:
        return []
    candidates = await extract_candidates(llm, transcript, max_items=max_items)
    if not candidates:
        return []
    return await remember_candidates(
        session,
        tenant_id,
        candidates=candidates,
        workspace_id=workspace_id,
        source_type=source_type,
        source_id=source_id,
        max_content_chars=int(
            getattr(settings, "memory_max_content_chars", 8000) or 8000
        ),
        max_entries=int(
            getattr(settings, "memory_max_entries_per_tenant", 2000) or 2000
        ),
        dedupe_threshold=float(getattr(settings, "memory_dedupe_threshold", 0.9)),
    )


def schedule_run_extraction(session: AsyncSession, run: Run) -> None:
    """run 完成时登记提交后抽取(P1-3):不依赖 outbox 投递。

    run 完成发生在事务里,直接 await LLM 会拖住事务;这里只登记回调,由会话边界
    (API 依赖 / jobs worker)在提交成功后执行。outbox 开启时交给 outbox 消费者,
    避免同一 run 被抽两次。
    """
    runtime = post_commit.runtime_of(session)
    if runtime is None or runtime.llm is None or runtime.settings is None:
        return
    settings = runtime.settings
    if not getattr(settings, "memory_enabled", True):
        return
    if not getattr(settings, "memory_auto_extract_enabled", False):
        return
    if getattr(settings, "outbox_enabled", False):
        return
    post_commit.register_after_commit(
        session, partial(_extract_run_memories, runtime, run.tenant_id, run.id)
    )


async def _extract_run_memories(
    runtime: post_commit.PostCommitRuntime, tenant_id: str, run_id: str
) -> None:
    """提交后回调:读已提交的 run/对话,按与 chat 同一口径节流后抽取。"""
    try:
        async with session_scope(runtime.session_factory) as session:
            run = await RunRepository(session).get_for_tenant(tenant_id, run_id)
            if run is None or not run.conversation_id:
                return
            settings = runtime.settings
            min_messages = int(
                getattr(settings, "memory_extraction_min_messages", 6) or 0
            )
            if min_messages <= 0:
                return
            message_repository = MessageRepository(session)
            count = await message_repository.count_for_conversation(
                tenant_id, run.conversation_id
            )
            if count % min_messages != 0:
                logger.debug(
                    "run memory extract throttled",
                    run_id=run_id,
                    conversation_id=run.conversation_id,
                    message_count=count,
                    min_messages=min_messages,
                )
                return
            messages = await message_repository.list_for_conversation(
                tenant_id, run.conversation_id
            )
            written = await extract_from_messages(
                session,
                runtime.llm,
                tenant_id=tenant_id,
                messages=messages,
                workspace_id=run.workspace_id,
                source_type="run",
                source_id=run_id,
                settings=settings,
            )
            if written:
                logger.info(
                    "run memories extracted",
                    run_id=run_id,
                    conversation_id=run.conversation_id,
                    count=len(written),
                )
    except Exception as exc:  # noqa: BLE001 - 提交后副作用失败不影响主链路
        logger.warning(
            "run memory extraction failed",
            run_id=run_id,
            error=f"{type(exc).__name__}: {exc}",
        )


async def consolidate(
    session: AsyncSession,
    tenant_id: str,
    *,
    workspace_id: str | None = None,
    threshold: float = 0.92,
    limit: int = _DEFAULT_CONSOLIDATE_LIMIT,
) -> int:
    """合并近重复记忆,返回软删除条数;pinned 永不删除。

    按 (工作区域, kind) 分组后贪心聚类:组内按保留优先级排序,依次并入第一个
    相似度达标的簇,否则自成一簇;每簇只留优先级最高的那条,其余软删。只合并
    同 kind 是刻意的——偏好与事实即使措辞相近,语义角色也不同。
    """
    repository = MemoryRepository(session)
    rows = await repository.list_active(
        tenant_id, workspace_id=workspace_id, limit=max(limit, 1)
    )
    if len(rows) < 2:
        return 0

    groups: dict[tuple[str, str | None], list[Memory]] = {}
    for memory in rows:
        groups.setdefault((memory.kind, _workspace_key(memory)), []).append(memory)

    deleted = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        clusters: list[list[Memory]] = []
        cluster_tokens: list[set[str]] = []
        for memory in sorted(members, key=_keep_priority):
            tokens = _tokens(memory.content)
            for index, reference in enumerate(cluster_tokens):
                if _similarity(tokens, reference) >= threshold:
                    clusters[index].append(memory)
                    break
            else:
                clusters.append([memory])
                cluster_tokens.append(tokens)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keeper = min(cluster, key=_keep_priority)
            for memory in cluster:
                if memory.id == keeper.id or memory.pinned:
                    continue
                memory.deleted_at = _utcnow()
                deleted += 1
    if deleted:
        await session.flush()
    return deleted
