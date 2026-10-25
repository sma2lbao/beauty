"""提交后回调:业务在事务里登记的副作用,等事务提交成功后再执行。

为什么需要这一层(P2 复核 P1-2/P1-3 的收敛):

- **长事务**:自动记忆抽取要读已提交的 run/消息,还要 await LLM;塞进请求事务会拖长
  事务并让响应等模型;
- **提交时机**:HTTP 请求的 session 由 `api/deps.get_session` 统一提交,jobs worker
  自己用 `session_scope`;只有这两个边界知道"什么时候算真的落库了";
- **不依赖 outbox**:outbox 默认关闭,但记忆抽取不应该跟着被关掉;提交后回调给了
  一条"进程内、不依赖事件投递"的路径。

约定:

- 回调**不得**使用登记时的 session(它已经提交/关闭),必须自建 session;
- 回调异常只 warning,不影响已经提交的业务结果和已经发出的响应;
- 回调在请求响应体发出之后执行(依赖 teardown 阶段),不增加客户端可见延迟。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

#: session.info 里的回调队列;key 带模块前缀避免与 outbox 等挂点撞名。
_CALLBACKS_KEY = "agent_orchestrator.post_commit_callbacks"
_RUNTIME_KEY = "agent_orchestrator.post_commit_runtime"

AfterCommitCallback = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class PostCommitRuntime:
    """会话边界可用的进程级依赖:提交后回调据此自建会话、调模型。"""

    session_factory: async_sessionmaker[AsyncSession]
    llm: Any = None
    settings: Any = None


def bind_runtime(session: AsyncSession, runtime: PostCommitRuntime) -> None:
    """把运行时依赖挂到 session.info(由 API 依赖/worker 在会话开始时调用)。

    业务层(如 run 完成、chat 回复落库)据此判断"当前有提交后回调能力";没有
    绑定时退回同步执行或跳过,保持旧的服务层直调语义。
    """
    session.info[_RUNTIME_KEY] = runtime


def runtime_of(session: AsyncSession) -> PostCommitRuntime | None:
    """读取当前会话绑定的运行时;无绑定(服务层直调/单测)返回 None。"""
    runtime = session.info.get(_RUNTIME_KEY)
    return runtime if isinstance(runtime, PostCommitRuntime) else None


def register_after_commit(session: AsyncSession, callback: AfterCommitCallback) -> None:
    """登记一个"提交成功后执行"的回调;同一会话可登记多个,按登记顺序执行。"""
    session.info.setdefault(_CALLBACKS_KEY, []).append(callback)


async def drain_after_commit(session: AsyncSession) -> None:
    """执行并清空回调队列;只在会话成功提交(commit)之后调用。

    回调串行执行:抽取类回调内部有自己的短事务与模型调用,并发只会让测试
    时序和限流更难解释。单个回调失败只 warning,不影响后续回调与已提交结果。
    """
    callbacks: list[AfterCommitCallback] = session.info.pop(_CALLBACKS_KEY, [])
    for callback in callbacks:
        try:
            await callback()
        except Exception as exc:  # noqa: BLE001 - 副作用失败不能反噬主链路
            logger.warning(
                "post-commit callback failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
