"""节点边界的协作式取消(design/recovery.md §9)。

图节点是纯函数,拿不到会话,也不该自己抛"用户中断";取消的**权威信号**始终
是库里的 `run.status = cancelled`(见 services/run_cancel.py)。这里只提供一个
探针端口:节点在安全边界(节点入口、每次模型调用前、每次工具执行前)问一句
"还该继续吗",答案是"已取消"就抛 :class:`RunCancelled`,让图在下一个边界停下。

为什么停在边界而不是硬打断:
- 永远停在两次副作用之间,不会把一次工具调用劈成两半;
- 已发生的写操作有跨 attempt 稳定幂等键兜底(S1),重放不会二次生效;
- 图停下后 checkpoint 停在最后一个完成节点,终态由取消方写好;执行边界读到
  run 已 cancelled 就不再覆盖(见 run_events._fail_run 的 is_superseded)。
"""

from __future__ import annotations

from agent_orchestrator.graph.runtime import GraphRuntime

#: 取消时的异常文案(落日志/审计用,不落 run.status_reason——那是取消方写的)。
CANCELLED_MESSAGE = "run 已取消,执行在节点边界停止"


class RunCancelled(Exception):
    """图执行在节点边界发现 run 已被取消:向上抛出,由调用边界收尾。"""

    def __init__(self, run_id: str | None = None) -> None:
        super().__init__(CANCELLED_MESSAGE)
        self.run_id = run_id


async def raise_if_cancelled(runtime: GraphRuntime) -> None:
    """问一次取消探针;已取消则抛 RunCancelled。

    未注入探针时(:data:`DEFAULT_RUNTIME`)恒为"未取消",直跑节点的单测
    与不关心取消的调用方行为完全不变。
    """
    if await runtime.ports.cancel.is_cancelled():
        raise RunCancelled()
