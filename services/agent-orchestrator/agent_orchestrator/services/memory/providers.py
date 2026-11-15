"""记忆提供方扩展点:协议、本地实现与构建/切换。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from common.logging import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # 仅用于类型标注,避免 config 与 services 的导入环。
    from agent_orchestrator.core.config import Settings

from .constants import LOGGER_NAME
from .recall import recall_block
from .store import delete_memory, write_memory

logger = get_logger(LOGGER_NAME)


class MemoryProvider(Protocol):
    """记忆提供方扩展点(P2):一期用 LocalMemoryProvider,后续可换 mem0/Zep。

    session 由调用方提供,provider 只做读写、不管理事务;这样 HTTP 请求、run、
    工具 handler 可以共用同一份实现,事务边界始终在调用侧。
    """

    async def recall(
        self,
        session: AsyncSession,
        account_id: str,
        query: str,
        *,
        workspace_id: str | None = None,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> str: ...

    async def remember(
        self,
        session: AsyncSession,
        account_id: str,
        content: str,
        *,
        kind: str = "note",
        scope: str = "account",
        workspace_id: str | None = None,
        source_type: str = "manual",
        source_id: str | None = None,
        pinned: bool = False,
        ttl_days: int | None = None,
    ) -> str: ...

    async def forget(
        self, session: AsyncSession, account_id: str, memory_id: str
    ) -> None: ...


class LocalMemoryProvider:
    """本地实现:直接转调本模块函数(无状态,可安全复用同一实例)。"""

    async def recall(
        self,
        session: AsyncSession,
        account_id: str,
        query: str,
        *,
        workspace_id: str | None = None,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> str:
        return await recall_block(
            session,
            account_id,
            query,
            workspace_id=workspace_id,
            limit=limit,
            max_chars=max_chars,
        )

    async def remember(
        self,
        session: AsyncSession,
        account_id: str,
        content: str,
        *,
        kind: str = "note",
        scope: str = "account",
        workspace_id: str | None = None,
        source_type: str = "manual",
        source_id: str | None = None,
        pinned: bool = False,
        ttl_days: int | None = None,
    ) -> str:
        memory = await write_memory(
            session,
            account_id,
            content=content,
            kind=kind,
            scope=scope,
            workspace_id=workspace_id,
            source_type=source_type,
            source_id=source_id,
            pinned=pinned,
            ttl_days=ttl_days,
        )
        return memory.id

    async def forget(
        self, session: AsyncSession, account_id: str, memory_id: str
    ) -> None:
        await delete_memory(session, account_id, memory_id)


#: 进程内默认提供方:未显式配置时复用同一个无状态实例。
_DEFAULT_MEMORY_PROVIDER: MemoryProvider | None = None
#: 容器装配的提供方:远程/自定义实现由启动期注册,读路径只读这一个入口。
_memory_provider: MemoryProvider | None = None


def configure_memory_provider(provider: MemoryProvider | None) -> None:
    """注册当前提供方(容器装配调用);传 None 等价于复位到本地默认。"""
    global _memory_provider
    _memory_provider = provider


def current_memory_provider() -> MemoryProvider:
    """取当前提供方;未注册时返回本地实现(零网络兜底)。"""
    global _DEFAULT_MEMORY_PROVIDER
    if _memory_provider is not None:
        return _memory_provider
    if _DEFAULT_MEMORY_PROVIDER is None:
        _DEFAULT_MEMORY_PROVIDER = LocalMemoryProvider()
    return _DEFAULT_MEMORY_PROVIDER


def reset_memory_provider() -> None:
    """清空注册与默认单例(测试隔离用,生产不做热切换)。"""
    global _DEFAULT_MEMORY_PROVIDER, _memory_provider
    _DEFAULT_MEMORY_PROVIDER = None
    _memory_provider = None


def build_memory_provider(settings: Settings | None = None) -> MemoryProvider:
    """按配置构造记忆提供方:默认本地,远端配置齐全才出网。

    provider 取值:
    - local:进程内表(默认);
    - remote:自有 HTTP 契约(RemoteMemoryProvider);
    - mem0 / zep:厂商适配(Mem0MemoryProvider / ZepMemoryProvider)。

    厂商适配缺 API key 时告警并回退本地,避免一条错配置让服务起不来;
    远端实现全部延迟导入,避免 services 模块之间的导入环。不传 settings 时
    保持旧行为返回本地实现——测试与脚本按此断言。
    """
    if settings is None:
        return LocalMemoryProvider()
    provider = (settings.memory_provider or "local").strip().lower()
    if provider == "remote" and settings.memory_remote_base_url:
        from agent_orchestrator.services.memory_remote import RemoteMemoryProvider

        return RemoteMemoryProvider(
            settings.memory_remote_base_url,
            timeout_seconds=settings.memory_remote_timeout_seconds,
            internal_secret=settings.internal_auth_secret,
        )
    if provider in {"mem0", "zep"}:
        if not settings.memory_vendor_api_key:
            logger.warning("memory vendor api key missing; falling back to local")
            return LocalMemoryProvider()
        from agent_orchestrator.services import memory_vendors

        if provider == "mem0":
            return memory_vendors.Mem0MemoryProvider(
                settings.memory_vendor_base_url or memory_vendors.MEM0_DEFAULT_BASE_URL,
                api_key=settings.memory_vendor_api_key,
                timeout_seconds=settings.memory_vendor_timeout_seconds,
            )
        return memory_vendors.ZepMemoryProvider(
            settings.memory_vendor_base_url or memory_vendors.ZEP_DEFAULT_BASE_URL,
            api_key=settings.memory_vendor_api_key,
            timeout_seconds=settings.memory_vendor_timeout_seconds,
        )
    return LocalMemoryProvider()
