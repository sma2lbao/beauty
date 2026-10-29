"""run 执行时的长期记忆召回:查询词构造与提示块拼装。

从 services/run_execution.py 拆出(门面仍由 run_execution.py 全量重导出):
只读依赖 memory_service,不反向依赖执行/审批模块,可被任意执行阶段复用。
"""

from __future__ import annotations

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.core.config import Settings
from agent_orchestrator.models.runs import Run
from agent_orchestrator.services import memory as memory_service

logger = get_logger(__name__)


def _memory_query(run: Run) -> str:
    """记忆召回查询词:目标为主、标题为辅(标题常含用户原话,更易命中偏好)。"""
    return " ".join(part for part in (run.goal, run.title) if part).strip()


async def _recall_memory_block(
    session: AsyncSession, run: Run, settings: Settings
) -> str:
    """召回长期记忆并拼成提示块(§6.7);失败降级空串,不阻断 run。

    两块内容:程序性约定(kind=procedure)无条件注入——它是"这个工作区的
    做法",不依赖查询词命中;相关性命中按 goal+title 检索。记忆是"锦上添花"
    的上下文:嵌入/检索/库任一处抖动都只该让交付少一点个性化,不该让用户
    看到 failed——所以这里吞异常只留 warning。
    """
    try:
        convention = await memory_service.convention_block(
            session,
            run.tenant_id,
            workspace_id=run.workspace_id,
            max_chars=settings.memory_convention_max_chars,
        )
        query = _memory_query(run)
        recall = ""
        if query:
            recall = await memory_service.recall_block(
                session,
                run.tenant_id,
                query,
                workspace_id=run.workspace_id,
                limit=settings.memory_recall_limit,
                max_chars=settings.memory_recall_max_chars,
            )
        return "\n\n".join(part for part in (convention, recall) if part)
    except Exception as exc:  # noqa: BLE001 - 记忆故障不阻断执行
        logger.warning(
            "memory recall failed",
            run_id=run.id,
            tenant_id=run.tenant_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return ""
