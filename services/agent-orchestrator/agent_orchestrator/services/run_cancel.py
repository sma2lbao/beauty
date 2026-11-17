"""run 取消(P0-2):非终态 → cancelled,在下一个节点边界真正停下。

取消语义:
- 状态立刻落 cancelled 并记 run.cancelled.v1,前端/SSE 立即看到终态;
- 还在排队的 job 直接置 cancelled,不再被 worker 领取;
- 已 running 的图调用在**下一个安全边界**被打断(S3,design/recovery.md §9):
  节点入口、每次模型调用前、每次工具执行前都会问一次取消探针
  (services/run_cancellation.RunCancellationProbe,读的是库里的 status),
  已取消则抛 graph.cancellation.RunCancelled,图在两次副作用之间停下——不会
  把一次工具调用劈成两半;已发生的写由 S1 稳定幂等键兜底,重放不二次生效;
- 执行边界回来时读库发现 cancelled,跳过终态覆盖(见 _apply_graph_result 与
  run_events._fail_run 的 is_superseded),取消方写的 status_reason 不被覆盖;
- 执行期内也能放弃等待(S6,graph.cancellation.guarded):模型补全、工具执行、
  流式增量的每一段按 0.25s 轮询探针,探到已取消就取消自己那一次等待,不必等
  长调用跑完才停;
- 顺带通知 tool-service 把该 run 的在飞调用标成 cancel_requested,让工具服务
  那边同样不再等结果(见本文件底部 configure_tool_cancel)。通知是尽力而为的
  旁路:编排器取消了自己的等待,工具服务晚一点收手也不影响刚才的语义。
- 跨进程同样成立:探针每次查库,不依赖进程内信号;进程内也不走旁路信号。

仍有的残留(属"取消的边界粒度",不是重复副作用):已经发到沙箱的命令不会被
打断,取消也不补偿已发生的写(与 S5 的台账+确认口径一致)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator import metrics
from agent_orchestrator.models.runs import Run
from agent_orchestrator.repositories import JobRepository
from agent_orchestrator.schemas.runs import RunStatus
from agent_orchestrator.services.run_events import append_event
from agent_orchestrator.services.run_lifecycle import require_run, require_transition

DEFAULT_REASON = "已取消"

#: 通知 tool-service 放弃在飞调用的钩子(进程级,由容器装配)。
#: 用 configure 模式而不是构造参数:取消入口分散(HTTP 端点、job 改道、重试),
#: 与 hooks_service.configure_hooks 同一套口径。
_TOOL_CANCEL_NOTIFIER: Callable[[str, str], Awaitable[None]] | None = None

#: 通知是尽力而为的旁路:工具服务不可达不该让取消本身失败。
NOTIFY_TIMEOUT_SECONDS = 2.0

logger = structlog.get_logger(__name__)


def configure_tool_cancel(
    notifier: Callable[[str, str], Awaitable[None]] | None,
) -> None:
    """装配"取消时通知 tool-service"的钩子;传 None 表示不通知。

    进程级单例:容器装配时写入,tool_client 为空或假客户端(测试注入)时写回
    None,避免上一个容器的装配泄漏到下一个。
    """

    global _TOOL_CANCEL_NOTIFIER
    _TOOL_CANCEL_NOTIFIER = notifier


async def _notify_tool_cancel(account_id: str, run_id: str) -> None:
    """通知工具服务放弃该 run 的在飞调用;失败只记日志,不影响取消结果。"""

    notifier = _TOOL_CANCEL_NOTIFIER
    if notifier is None:
        return
    try:
        await asyncio.wait_for(notifier(account_id, run_id), NOTIFY_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - 旁路通知不阻断取消
        logger.warning("tool cancel notify failed", run_id=run_id, error=str(exc))


async def cancel_run(
    session: AsyncSession,
    account_id: str,
    run_id: str,
    reason: str | None = None,
) -> Run:
    """取消一个非终态 run;reason 落 status_reason 与 run.cancelled.v1 载荷。"""
    run = await require_run(session, account_id, run_id)
    require_transition(run, "cancel")
    text = (reason or "").strip() or DEFAULT_REASON
    run.status = RunStatus.CANCELLED.value
    run.status_reason = text[:500]
    cancelled_jobs = await JobRepository(session).cancel_queued_for_run(
        run.account_id, run.id
    )
    await append_event(
        session,
        run,
        "run.cancelled.v1",
        {"reason": text, "cancelled_jobs": cancelled_jobs},
    )
    metrics.observe_run_outcome(status=RunStatus.CANCELLED.value)
    # 终态已落库再看工具服务:通知失败也不回滚取消(尽力而为的旁路)。
    await _notify_tool_cancel(run.account_id, run.id)
    # 被取消的子 run 也要唤醒父 run(L2):父 run 拿回执继续往下走,而不是
    # 永久停在 awaiting_subagent;取消的是父 run 时这里自然 no-op。
    from agent_orchestrator.services import subagent_wake

    await subagent_wake.notify_parent(session, run)
    return run
