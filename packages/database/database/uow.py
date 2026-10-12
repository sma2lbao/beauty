"""工作单元(架构 §7):显式事务边界 + 仓储注册。

HTTP 端点的边界仍是 FastAPI 依赖 + `session_scope`(一请求一事务);
UoW 服务的是**需要在一个事务里跨多个仓储**的场景:后台任务、运维脚本、
批量导入、集成测试。

    async with UnitOfWork(factory, repositories=(UserRepository,)) as uow:
        await uow.repo(UserRepository).add(User(...))
        # 正常退出即提交;抛异常自动回滚

约定:

- 退出时无异常 → commit;有异常 → rollback 后再抛出(不吞错);
- 仓储实例按类型缓存,同一事务内共享 session —— 跨仓储的一致性由此保证;
- 不提供嵌套事务/Savepoint:阶段 A 不需要,需要时另开显式 API。
"""

from __future__ import annotations

from collections.abc import Iterable
from types import TracebackType
from typing import Any, Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.repository import Repository


class UnitOfWork:
    """一个工作单元 = 一个 session + 一组仓储。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        repositories: Iterable[type[Repository[Any]]] = (),
    ) -> None:
        self._factory = factory
        self._repository_types = tuple(repositories)
        self._repositories: dict[type[Repository[Any]], Repository[Any]] = {}
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self.session = self._factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        assert self.session is not None  # __aenter__ 已保证
        try:
            if exc_type is None:
                await self.session.commit()
            else:
                await self.session.rollback()
        finally:
            await self.session.close()
            self.session = None
            self._repositories.clear()
        return False  # 不吞异常

    def repo(self, repository_type: type[Repository[Any]]) -> Any:
        """取(并缓存)仓储实例;类型未在构造时登记也能按需创建。"""
        if self.session is None:
            raise RuntimeError("UnitOfWork 必须在 async with 块内使用")
        instance = self._repositories.get(repository_type)
        if instance is None:
            instance = repository_type(self.session)
            self._repositories[repository_type] = instance
        return instance
