"""仓储基类(架构 §7):把"租户过滤 + 404 语义"收敛到一处。

约定:

- 业务查询**一律经仓储**;`select()` 是留给复杂 SQL 的逃生口,但任何业务实体
  查询都必须带租户过滤——多租户安全的下限由基类保证,而不是靠每处自觉;
- `require()` 找不到时抛 `common.errors.NotFoundError`(404),错误码/文案由子类
  声明(`not_found_code`/`not_found_message`),各服务的错误契约因此天然一致;
- 仓储**不提交**:事务边界仍由 `database.session.session_scope` 或 `UnitOfWork` 负责
  ——这样"一个事务里跨多个仓储"才有可能;
- 两个基类对应两种表:`TenantRepository` 用于带 `tenant_id` 的业务表,
  `Repository` 用于本身即租户实体(如组织表)或全局表。

示例:

    class RunRepository(TenantRepository[Run]):
        model = Run
        not_found_code = "run.not_found"
        not_found_message = "run 不存在"

    run = await RunRepository(session).require(tenant_id, run_id)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from common.errors import NotFoundError
from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption
from sqlalchemy.sql.elements import ColumnElement

from database.base import Base, TenantMixin

EntityT = TypeVar("EntityT", bound=Base)


class Repository(Generic[EntityT]):
    """无租户语义的仓储(组织表、全局配置等)。"""

    model: type[EntityT]
    not_found_code: str = "resource.not_found"
    not_found_message: str = "资源不存在"
    # 404 details 里主键用的键名:各服务原有契约不同(run_id/workspace_id/…),
    # 用声明保持**零契约变更**;统一成 "id" 是后续可以单独做的一次收敛。
    id_detail_key: str = "id"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- 查询 ----

    def select(self, *criteria: ColumnElement[bool]) -> Select[tuple[EntityT]]:
        """逃生口:自己拼 `select`,再交给 `scalars()`/`all()` 执行。"""
        return select(self.model).where(*criteria)

    async def get(self, entity_id: str) -> EntityT | None:
        return await self.session.get(self.model, entity_id)

    async def require(
        self, entity_id: str, *, details: dict[str, Any] | None = None
    ) -> EntityT:
        entity = await self.get(entity_id)
        if entity is None:
            raise self._not_found(entity_id, details)
        return entity

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        order_by: Sequence[Any] | None = None,
        options: Sequence[ORMOption] | None = None,
    ) -> list[EntityT]:
        statement = select(self.model).offset(offset)
        if order_by is not None:
            statement = statement.order_by(*order_by)
        if limit is not None:
            statement = statement.limit(limit)
        if options is not None:
            statement = statement.options(*options)
        return list(await self.session.scalars(statement))

    async def count(self, *criteria: ColumnElement[bool]) -> int:
        statement = select(func.count()).select_from(self.model).where(*criteria)
        return int(await self.session.scalar(statement) or 0)

    async def exists(self, *criteria: ColumnElement[bool]) -> bool:
        return await self.count(*criteria) > 0

    # ---- 写入(不提交) ----

    async def add(self, entity: EntityT, *, flush: bool = True) -> EntityT:
        self.session.add(entity)
        if flush:
            await self.session.flush()
        return entity

    async def delete(self, entity: EntityT, *, flush: bool = True) -> None:
        await self.session.delete(entity)
        if flush:
            await self.session.flush()

    def _not_found(
        self, entity_id: str, details: dict[str, Any] | None = None
    ) -> NotFoundError:
        return NotFoundError(
            self.not_found_code,
            self.not_found_message,
            details={self.id_detail_key: entity_id, **(details or {})},
        )


class TenantRepository(Repository[EntityT]):
    """租户隔离仓储:所有读取自动带 `tenant_id` 过滤。

    绑定 `model` 的类必须混入 `TenantMixin`(类定义时即校验,防止漏隔离)。
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        model = getattr(cls, "model", None)
        if model is not None and not issubclass(model, TenantMixin):
            raise TypeError(
                f"{cls.__name__}.model={model.__name__} 缺少 TenantMixin:"
                "租户仓储只能绑定带 tenant_id 的表"
            )

    def scoped(
        self, tenant_id: str, *criteria: ColumnElement[bool]
    ) -> Select[tuple[EntityT]]:
        return select(self.model).where(self.model.tenant_id == tenant_id, *criteria)

    async def list_scoped(
        self,
        tenant_id: str,
        *criteria: ColumnElement[bool],
        limit: int | None = None,
        offset: int = 0,
        order_by: Sequence[Any] | None = None,
    ) -> list[EntityT]:
        statement = self.scoped(tenant_id, *criteria).offset(offset)
        if order_by is not None:
            statement = statement.order_by(*order_by)
        if limit is not None:
            statement = statement.limit(limit)
        return list(await self.session.scalars(statement))

    async def get_for_tenant(self, tenant_id: str, entity_id: str) -> EntityT | None:
        return await self.session.scalar(
            self.scoped(tenant_id, self.model.id == entity_id)
        )

    async def require_for_tenant(
        self,
        tenant_id: str,
        entity_id: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> EntityT:
        """按租户取实体;不存在(或不属于该租户)一律 404——不泄漏存在性。

        注意用 404 而非 403:跨租户探测不应该能区分"存在但无权"与"不存在"。
        """
        entity = await self.get_for_tenant(tenant_id, entity_id)
        if entity is None:
            raise self._not_found(entity_id, details)
        return entity

    async def count_for_tenant(self, tenant_id: str) -> int:
        return await self.count(self.model.tenant_id == tenant_id)

    async def exists_for_tenant(
        self, tenant_id: str, *criteria: ColumnElement[bool]
    ) -> bool:
        """租户内是否存在满足条件的行(如"同 subject 的成员已存在")。"""
        return await self.count(self.model.tenant_id == tenant_id, *criteria) > 0

    async def delete_for_tenant(self, tenant_id: str, entity_id: str) -> None:
        """按租户删除(不存在即 404),绕开"先查再删"的样板。"""
        entity = await self.require_for_tenant(tenant_id, entity_id)
        await self.delete(entity)

    async def delete_all_for_tenant(self, tenant_id: str) -> int:
        """清空该租户的数据(如删除文档时连带清分块),返回删除行数。"""
        result = await self.session.execute(
            delete(self.model).where(self.model.tenant_id == tenant_id)
        )
        return int(result.rowcount or 0)
