"""工具目录的按账号进程级缓存(自 tool_client.py 拆出)。

目录按账号拉取:内置工具对所有账号相同,但连接器工具取决于该账号是否有
active+enabled 的连接,因此缓存键必须是 account_id。首次 ``ensure`` 拉一次
目录、失败下次重试;``refresh`` 供显式刷新。多请求共享同一实例,靠
``asyncio.Lock`` 保证并发只发一次请求(跨账号仍串行化,目录请求很轻)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from common.tools import ToolDescriptor

if TYPE_CHECKING:
    from agent_orchestrator.services.tool_client_http import ToolClient


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    """单个账号已加载的目录快照。"""

    descriptors: tuple[ToolDescriptor, ...]
    version: str


class ToolCatalog:
    """按账号的工具目录缓存(多请求共享;失败不缓存)。"""

    def __init__(self) -> None:
        self._entries: dict[str, _CatalogEntry] = {}
        self._lock = asyncio.Lock()

    def descriptors(self, account_id: str) -> tuple[ToolDescriptor, ...]:
        """该账号可见的工具;未加载时为空(调用方应先 ensure)。"""

        entry = self._entries.get(account_id)
        return entry.descriptors if entry is not None else ()

    def version(self, account_id: str) -> str:
        entry = self._entries.get(account_id)
        return entry.version if entry is not None else ""

    def loaded(self, account_id: str) -> bool:
        return account_id in self._entries

    async def ensure(self, client: ToolClient, account_id: str) -> None:
        """确保该账号目录已加载:并发调用只发一次请求,失败后下次重试。"""

        if account_id in self._entries:
            return
        async with self._lock:
            if account_id in self._entries:
                return
            tools, version = await client.list_tools(account_id)
            self._entries[account_id] = _CatalogEntry(tools, version)

    async def refresh(self, client: ToolClient, account_id: str) -> None:
        """强制刷新该账号目录(控制台/运维显式触发;P0 不自动轮询)。"""

        async with self._lock:
            tools, version = await client.list_tools(account_id)
            self._entries[account_id] = _CatalogEntry(tools, version)
