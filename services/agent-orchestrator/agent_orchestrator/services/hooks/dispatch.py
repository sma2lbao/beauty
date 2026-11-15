"""钩子快照与调用入口:装载、按事件跑钩子、提交后通知。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_orchestrator.graph.ports import HookOutcome
from agent_orchestrator.models.hook import KIND_WEBHOOK, AccountHook
from agent_orchestrator.repositories import HookRepository
from agent_orchestrator.services import post_commit

from .config import current_config
from .runner import _DEFAULT_RUNNER

# ---------------------------------------------------------------- 快照与调用


def _snapshot(row: AccountHook) -> dict[str, Any]:
    """ORM 行 → 图状态可序列化的 hook 快照(secret 必须带,签名要用)。"""
    return {
        "id": row.id,
        "account_id": row.account_id,
        "event": row.event,
        "matcher": row.matcher or "*",
        "kind": row.kind or KIND_WEBHOOK,
        "target": row.target,
        "secret": row.secret,
        "timeout_seconds": int(row.timeout_seconds or 3),
        "enabled": bool(row.enabled),
    }


async def load_hooks(
    session: AsyncSession, account_id: str, *, event: str | None = None
) -> list[dict[str, Any]]:
    """读账号 enabled hooks 快照(created_at, id 升序);图节点/chat 只消费快照。"""
    rows = await HookRepository(session).list_for_account(
        account_id, event=event, enabled_only=True
    )
    return [_snapshot(row) for row in rows]


async def run_hooks(
    hooks: Sequence[Mapping[str, Any]],
    event: str,
    payload: Mapping[str, Any],
) -> HookOutcome:
    """对一份已有快照求值(chat 等已持快照的服务层挂点用)。

    图内走 ``graph.ports.HookPort``(同一 runner),这里给不方便注入端口的
    服务层留的直调入口;未启用配置时内部直接返回 allow。
    """
    return await _DEFAULT_RUNNER.run(hooks, event, payload)


async def run_for_account(
    session: AsyncSession,
    account_id: str,
    event: str,
    payload: Mapping[str, Any],
) -> HookOutcome:
    """服务层挂点(chat/确认/压缩)的一次性调用:读快照 → 执行。"""
    hooks = await load_hooks(session, account_id)
    return await _DEFAULT_RUNNER.run(hooks, event, payload)


async def notify_after_commit(
    session: AsyncSession,
    account_id: str,
    event: str,
    payload: Mapping[str, Any],
) -> None:
    """会话类事件(§6.3):提交后 best-effort,失败只 warning、不回滚业务。

    有 post_commit 运行时(HTTP 请求路径)就注册到提交后执行;服务层直调
    (单测/脚本)没有运行时,直接 await——两条路径都不阻塞业务返回语义。
    """
    hooks = await load_hooks(session, account_id)
    if not hooks or not current_config().enabled:
        return

    async def _send() -> None:
        await _DEFAULT_RUNNER.run(hooks, event, payload)

    if post_commit.runtime_of(session) is None:
        await _send()
        return
    post_commit.register_after_commit(session, _send)
