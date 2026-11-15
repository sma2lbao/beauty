"""跨实例归属:登记、接管、续租与孤儿回收。

会话输出仍只活在持有实例内存里,登记表只回答两件事:

1. 这个 session_id 现在归谁(租约未过期就是"别人在管",给明确 409);
2. 持有者消失后,能不能由本实例接管(provider 支持 `attach_shell` 就可以,
   e2b 的 `pty.connect` 能重连到已有 pty,输出接着往下走)。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from common.errors import AppError, ConflictError, NotFoundError, UpstreamError

from workspace.services.sandbox_provider import SandboxProvider
from workspace.services.shell_sessions.registry import (
    ShellSessionClaim,
    ShellSessionRegistry,
)
from workspace.services.shell_sessions.state import _new_decoder, _ShellSessionState

logger = structlog.get_logger(__name__)


class _PlacementMixin:
    """会话归属判定与登记表维护(由 ShellSessionManager 组合)。"""

    # 以下属性由 ShellSessionManager.__init__ 注入
    _registry: ShellSessionRegistry | None
    _provider: SandboxProvider | None
    _instance_id: str
    _clock: Callable[[], float]
    _lease_seconds: float
    _closed_retention_seconds: float
    _sessions: dict[tuple[str, str, str], _ShellSessionState]
    _persist: set[asyncio.Task[None]]

    async def _ensure(
        self, account_id: str, sandbox_id: str, session_id: str
    ) -> _ShellSessionState:
        """拿到可用的本地会话状态;不在本实例时按登记表判归属并尝试接管。"""
        state = self._sessions.get((account_id, sandbox_id, session_id))
        if state is not None:
            return state
        if self._registry is None:
            raise NotFoundError(
                "sandbox.session_not_found",
                "shell 会话不存在",
                details={"session_id": session_id},
            )
        claim = await self._registry.lookup(account_id, sandbox_id, session_id)
        if claim is None:
            raise NotFoundError(
                "sandbox.session_not_found",
                "shell 会话不存在",
                details={"session_id": session_id},
            )
        if claim.status == "closed":
            raise ConflictError("sandbox.session_closed", "shell 会话已结束")
        if claim.status == "lost":
            raise ConflictError(
                "sandbox.session_lost",
                "shell 会话已失联(持有实例已消失且无法接管)",
                details={"session_id": session_id},
            )
        if claim.owner_instance != self._instance_id and not claim.lease_expired():
            raise ConflictError(
                "sandbox.session_elsewhere",
                "shell 会话由其他实例持有",
                details={
                    "session_id": session_id,
                    "owner_instance": claim.owner_instance,
                    "provider": claim.provider,
                    "resumable": self._can_resume(claim),
                },
            )
        return await self._takeover(claim)

    def _can_resume(self, claim: ShellSessionClaim) -> bool:
        """这个会话能不能被本实例接管:同 provider、有 pid、provider 支持 attach。"""
        provider = self._provider
        if provider is None or claim.provider_pid is None:
            return False
        if claim.provider != getattr(provider, "provider", ""):
            return False
        return callable(getattr(provider, "attach_shell", None))

    async def _takeover(self, claim: ShellSessionClaim) -> _ShellSessionState:
        provider = self._provider
        if provider is None or not self._can_resume(claim):
            await self._mark_lost(claim, reason="provider 不支持接管")
            raise ConflictError(
                "sandbox.session_lost",
                "持有实例已消失,且该 provider 不支持接管这个会话",
                details={"session_id": claim.session_id, "provider": claim.provider},
            )
        assert claim.provider_pid is not None
        if self._registry is not None and not await self._registry.adopt(claim):
            raise ConflictError(
                "sandbox.session_elsewhere",
                "shell 会话已被其他实例接管",
                details={"session_id": claim.session_id},
            )
        try:
            shell = await provider.attach_shell(
                claim.runtime_name, claim.provider_pid, cols=claim.cols, rows=claim.rows
            )
        except AppError as exc:
            await self._mark_lost(claim, reason=exc.code)
            raise ConflictError(
                "sandbox.session_lost",
                f"接管会话失败:{exc.message}",
                details={"session_id": claim.session_id},
            ) from exc
        except Exception as exc:  # noqa: BLE001 - provider 的异常形态不受控
            await self._mark_lost(claim, reason="attach_failed")
            raise UpstreamError(
                "sandbox.provider_error",
                "接管会话失败",
                details={"session_id": claim.session_id},
            ) from exc
        state = _ShellSessionState(
            account_id=claim.account_id,
            sandbox_id=claim.sandbox_id,
            session_id=claim.session_id,
            runtime_name=claim.runtime_name,
            provider_name=claim.provider,
            cwd=claim.cwd,
            cols=claim.cols,
            rows=claim.rows,
            created_at=claim.created_at,
            shell=shell,
            tty=claim.tty,
            decoder=_new_decoder(),
            last_activity=self._clock(),
        )
        self._sessions[state.key] = state
        state.reader = asyncio.create_task(
            self._pump(state), name=f"shell-session-{claim.session_id}"
        )
        logger.info(
            "shell_session.resumed",
            account_id=claim.account_id,
            sandbox_id=claim.sandbox_id,
            session_id=claim.session_id,
            previous_owner=claim.owner_instance,
            instance_id=self._instance_id,
        )
        return state

    async def _mark_lost(self, claim: ShellSessionClaim, *, reason: str) -> None:
        if self._registry is None:
            return
        with contextlib.suppress(Exception):
            await self._registry.mark_lost(claim, reason=reason)

    async def _register(self, state: _ShellSessionState) -> None:
        if self._registry is None:
            return
        try:
            await self._registry.register(
                account_id=state.account_id,
                sandbox_id=state.sandbox_id,
                session_id=state.session_id,
                provider=state.provider_name,
                runtime_name=state.runtime_name,
                provider_pid=getattr(state.shell, "pid", None),
                cwd=state.cwd,
                cols=state.cols,
                rows=state.rows,
                tty=state.tty,
                created_at=state.created_at,
            )
        except Exception:
            # 登记失败就别留下没人认领的 shell:关掉再往上抛
            with contextlib.suppress(Exception):
                await state.shell.close()
            raise

    async def _active_count(self, account_id: str, sandbox_id: str) -> int:
        if self._registry is not None:
            with contextlib.suppress(Exception):
                return await self._registry.count_active(account_id, sandbox_id)
        return sum(
            1
            for state in self._sessions.values()
            if state.account_id == account_id
            and state.sandbox_id == sandbox_id
            and not state.closed
        )

    def _on_session_closed(self, state: _ShellSessionState) -> None:
        """读循环收尾时把 closed 状态落回登记表(不能在读循环里 await)。"""
        self._spawn(self._persist_closed(state))

    async def _persist_closed(self, state: _ShellSessionState) -> None:
        if self._registry is None:
            return
        try:
            await self._registry.mark_closed([state.key], exit_code=state.exit_code)
        except Exception as exc:  # noqa: BLE001 - 登记收尾失败只留痕
            logger.warning(
                "shell_session.persist_failed",
                session_id=state.session_id,
                error=str(exc)[:300],
            )

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """在后台跑一个登记表写;进程退出时由 shutdown 统一等。"""
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._persist.add(task)
        task.add_done_callback(self._persist.discard)

    async def _renew_leases(self) -> int:
        if self._registry is None:
            return 0
        keys = [state.key for state in self._sessions.values() if not state.closed]
        try:
            return await self._registry.touch(keys)
        except Exception as exc:  # noqa: BLE001 - 续租失败只降级,不中断 sweep
            logger.warning("shell_session.renew_failed", error=str(exc)[:300])
            return 0

    async def _reclaim_orphans(self) -> int:
        """回收租约过期且不是本实例在管的会话(持有实例已经不在)。"""
        if self._registry is None:
            return 0
        try:
            claims = await self._registry.list_stale(limit=50)
        except Exception as exc:  # noqa: BLE001 - 回收失败下一轮再试
            logger.warning("shell_session.reclaim_failed", error=str(exc)[:300])
            return 0
        reaped = 0
        for claim in claims:
            local = self._sessions.get(
                (claim.account_id, claim.sandbox_id, claim.session_id)
            )
            if local is not None and not local.closed:
                # 自己还在管(续租刚失败),不能当孤儿收掉
                continue
            if self._provider is not None and self._can_resume(claim):
                with contextlib.suppress(Exception):
                    orphan = await self._provider.attach_shell(
                        claim.runtime_name,
                        int(claim.provider_pid or 0),
                        cols=claim.cols,
                        rows=claim.rows,
                    )
                    await orphan.close()
            await self._mark_lost(claim, reason="owner lease expired")
            reaped += 1
        with contextlib.suppress(Exception):
            await self._registry.purge_closed(
                retention_seconds=self._closed_retention_seconds
            )
        return reaped
