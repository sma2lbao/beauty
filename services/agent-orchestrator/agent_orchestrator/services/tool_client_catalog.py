"""工具目录的进程级缓存(自 tool_client.py 拆出)。

首次 ``ensure`` 拉一次目录、失败下次重试;``refresh`` 供显式刷新。多请求共享
同一实例,靠 ``asyncio.Lock`` 保证并发只发一次请求。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from common.tools import ToolDescriptor

if TYPE_CHECKING:
    from agent_orchestrator.services.tool_client_http import ToolClient


class ToolCatalog:
    """进程级工具目录缓存(多请求共享;失败不缓存)。"""

    def __init__(self) -> None:
        self._descriptors: tuple[ToolDescriptor, ...] = ()
        self._version = ""
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return self._descriptors

    @property
    def version(self) -> str:
        return self._version

    @property
    def loaded(self) -> bool:
        return self._loaded

    async def ensure(self, client: ToolClient) -> None:
        """确保目录已加载:并发调用只发一次请求,失败后下次重试。"""

        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            tools, version = await client.list_tools()
            self._descriptors = tools
            self._version = version
            self._loaded = True

    async def refresh(self, client: ToolClient) -> None:
        """强制刷新目录(控制台/运维显式触发;P0 不自动轮询)。"""

        async with self._lock:
            tools, version = await client.list_tools()
            self._descriptors = tools
            self._version = version
            self._loaded = True
