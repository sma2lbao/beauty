"""仓储基类:租户隔离、404 语义、逃生口、"不提交"契约。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from common.errors import NotFoundError
from sqlalchemy import JSON, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from database.repository import Repository, TenantRepository
from database.session import create_session_factory, session_scope


class Widget(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """测试用租户表。"""

    __tablename__ = "widgets"

    name: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WidgetRepository(TenantRepository[Widget]):
    model = Widget
    not_found_code = "widget.not_found"
    not_found_message = "组件不存在"
    id_detail_key = "widget_id"


class GlobalThing(UUIDMixin, TimestampMixin, Base):
    """测试用全局表(无 tenant_id)。"""

    __tablename__ = "global_things"

    name: Mapped[str] = mapped_column(String(50))


class ThingRepository(Repository[GlobalThing]):
    model = GlobalThing
    not_found_code = "thing.not_found"
    not_found_message = "对象不存在"


@pytest.fixture()
async def factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/repo.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await factory.engine.dispose()


async def test_tenant_isolation(factory) -> None:
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        await repo.add(Widget(tenant_id="t-1", name="甲的"))
        await repo.add(Widget(tenant_id="t-2", name="乙的"))
        await repo.add(Widget(tenant_id="t-1", name="甲的第二个"))

        assert await repo.count_for_tenant("t-1") == 2
        assert [
            w.name for w in await repo.list_scoped("t-1", order_by=[Widget.name])
        ] == [
            "甲的",
            "甲的第二个",
        ]
        assert [w.name for w in await repo.list_scoped("t-2")] == ["乙的"]


async def test_cross_tenant_lookup_is_404_not_403(factory) -> None:
    """跨租户探测拿到 404:不泄漏"存在但无权"这一信息。"""
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        widget = await repo.add(Widget(tenant_id="t-1", name="甲的"))

    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        assert await repo.get_for_tenant("t-1", widget.id) is not None
        with pytest.raises(NotFoundError) as exc_info:
            await repo.require_for_tenant("t-2", widget.id)

    assert exc_info.value.status == 404
    assert exc_info.value.code == "widget.not_found"
    assert exc_info.value.details == {"widget_id": widget.id}  # 契约键名可声明


async def test_require_missing_and_plain_repository(factory) -> None:
    async with session_scope(factory) as session:
        with pytest.raises(NotFoundError) as widget_exc:
            await WidgetRepository(session).require_for_tenant("t-1", "nope")
        assert widget_exc.value.code == "widget.not_found"

        thing_repo = ThingRepository(session)
        thing = await thing_repo.add(GlobalThing(name="全局"))
        assert (await thing_repo.require(thing.id)).name == "全局"
        with pytest.raises(NotFoundError) as thing_exc:
            await thing_repo.require("nope")
        assert thing_exc.value.code == "thing.not_found"


async def test_repository_does_not_commit(factory) -> None:
    """仓储只 flush,不提交:另一个 session 在提交前看不到它。"""
    async with session_scope(factory) as session:
        await WidgetRepository(session).add(Widget(tenant_id="t-1", name="未提交"))
        async with session_scope(factory) as other:
            assert await WidgetRepository(other).count_for_tenant("t-1") == 0

    async with session_scope(factory) as session:
        assert await WidgetRepository(session).count_for_tenant("t-1") == 1


async def test_escape_hatch_and_json_column(factory) -> None:
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        await repo.add(Widget(tenant_id="t-1", name="带数据", payload={"n": 1}))
        statement = repo.scoped("t-1", Widget.name == "带数据")
        found = await session.scalar(statement)

        assert found is not None
        assert found.payload == {"n": 1}
        # 逃生口仍是租户安全的写法:换租户查不到
        assert await session.scalar(repo.scoped("t-2", Widget.name == "带数据")) is None


async def test_list_limit_offset_and_order(factory) -> None:
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        for index in range(3):
            await repo.add(Widget(tenant_id="t-1", name=f"w{index}"))

        newest = await repo.list_scoped(
            "t-1", order_by=[Widget.created_at.desc(), Widget.id.desc()], limit=2
        )
        assert len(newest) == 2
        # 用 name 排序做确定性断言(created_at 可能落在同一微秒,id 兜底不稳定)
        tail = await repo.list_scoped("t-1", order_by=[Widget.name], offset=2)
        assert [w.name for w in tail] == ["w2"]


async def test_delete_helpers(factory) -> None:
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        first = await repo.add(Widget(tenant_id="t-1", name="甲"))
        await repo.add(Widget(tenant_id="t-1", name="乙"))
        await repo.add(Widget(tenant_id="t-2", name="丙"))

    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        await repo.delete_for_tenant("t-1", first.id)
        assert await repo.count_for_tenant("t-1") == 1
        with pytest.raises(NotFoundError):
            await repo.delete_for_tenant("t-1", first.id)  # 再删即 404

    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        assert await repo.delete_all_for_tenant("t-1") == 1
        assert await repo.count_for_tenant("t-1") == 0
        assert await repo.count_for_tenant("t-2") == 1  # 别租户不受影响


def test_tenant_repository_rejects_model_without_tenant_mixin() -> None:
    """类定义时就拦住漏了 TenantMixin 的表——这是多租户安全的下限。"""
    with pytest.raises(TypeError, match="缺少 TenantMixin"):

        class BadRepository(TenantRepository[GlobalThing]):
            model = GlobalThing
