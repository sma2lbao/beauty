"""工具执行上下文(ToolContext):把租户/run/会话带进工具 handler。

工具 handler 是"模型接触外部世界的唯一通道",但注册表只按名字执行,
拿不到调用来源。stdio MCP 需要按**对话级**复用沙箱(P2):同一个对话里
多次调工具应命中同一个沙箱,不同对话/不同 run 必须隔离。这里用
contextvars 在调用边界把来源塞进去,handler 里 `current_tool_context()`
即可读到——不需要改 ToolRegistry.execute 的签名。

约束:

- 用它的人只允许 `with bind_tool_context(ctx):` 的短生命周期写法,
  退出即 reset(`ContextVar.reset`),不会跨请求/跨 run 泄漏;
- contextvars 在 async 任务里随 await 传播(同一个 Task 内),因此图节点、
  chat 工具循环里的 handler 都能读到;
- 缺上下文时返回 None:handler 退化为 `anonymous` 作用域(沙箱不复用,
  但功能不中断)。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    """一次工具调用所属的租户 / run / 对话(全部可选,租户必有)。"""

    tenant_id: str
    run_id: str | None = None
    conversation_id: str | None = None
    # 对话消息显式引用的 MCP server:非空时只装配这些 server 的工具
    mcp_server_ids: tuple[str, ...] = ()

    @property
    def sandbox_scope(self) -> str:
        """沙箱复用作用域:优先对话(对话级常驻),退化到 run,最后 anonymous。

        同一对话的多次工具调用命中同一个沙箱;没有对话的 run(如 API 直接
        触发)按 run 隔离;两者都缺(如测试/脚本)退化为 `anonymous`。
        """
        if self.conversation_id:
            return self.conversation_id
        if self.run_id:
            return f"run:{self.run_id}"
        return "anonymous"


_current: ContextVar[ToolContext | None] = ContextVar("tool_context", default=None)


@contextmanager
def bind_tool_context(ctx: ToolContext) -> Iterator[ToolContext]:
    """在 with 块内绑定当前工具上下文;退出时恢复上一层(不泄漏)。"""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def current_tool_context() -> ToolContext | None:
    """读当前工具上下文;不在调用边界内(如脚本直调)返回 None。"""
    return _current.get()
