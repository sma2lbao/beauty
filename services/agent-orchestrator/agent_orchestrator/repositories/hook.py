"""账号 hooks 仓储(按域拆分)。"""

from __future__ import annotations

from database.repository import AccountRepository

from agent_orchestrator.models.hook import AccountHook


class HookRepository(AccountRepository[AccountHook]):
    """账号 hook 的增删改查:跨账号一律 404(hooks.not_found)。"""

    model = AccountHook
    not_found_code = "hooks.not_found"
    not_found_message = "hook 不存在"
    id_detail_key = "hook_id"

    async def list_for_account(
        self, account_id: str, *, event: str | None = None, enabled_only: bool = False
    ) -> list[AccountHook]:
        """按 (created_at, id) 升序:串行执行顺序与用户创建顺序一致。"""
        criteria = []
        if event is not None:
            criteria.append(AccountHook.event == event)
        if enabled_only:
            criteria.append(AccountHook.enabled.is_(True))
        return await self.list_scoped(
            account_id,
            *criteria,
            order_by=[AccountHook.created_at, AccountHook.id],
        )
