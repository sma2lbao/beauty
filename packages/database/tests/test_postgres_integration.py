"""Postgres 集成(架构 §12):优先 TEST_POSTGRES_URL,其次 testcontainers,否则跳过。

为什么值得真跑:sqlite 与 Postgres 在两处行为不同,仓储基类必须两边都对——
① 时区:sqlite 读回 datetime 不带 tzinfo(服务里靠"比较同一时刻"绕开),
   Postgres 保留时区;
② 排序:sqlite 的 CURRENT_TIMESTAMP 只有秒级精度,同秒行序不稳;
   Postgres 上 `created_at` 是完整时间戳。

本沙箱没有 Docker 守护进程,也没有本地 postgres 二进制,因此这里默认
**skipped**(跳过原因是显式文案,不是静默通过);在装了 Docker 或提供
TEST_POSTGRES_URL 的机器上会真跑:

    TEST_POSTGRES_URL=postgresql+asyncpg://beauty:beauty@localhost:5432/beauty \\
      npx nx test database
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.base import Base
from database.session import create_session_factory, session_scope
from tests.test_repository import Widget, WidgetRepository

POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """解析集成测试用的 Postgres:环境变量优先,其次 testcontainers,否则跳过。"""
    url = os.environ.get("TEST_POSTGRES_URL")
    if url:
        yield url
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover - 取决于环境
        pytest.skip(f"需要 TEST_POSTGRES_URL 或 testcontainers:{exc}")

    try:
        container = PostgresContainer(POSTGRES_IMAGE, driver="asyncpg")
        container.start()
    except Exception as exc:  # pragma: no cover - 取决于环境
        pytest.skip(f"Docker 不可用,跳过 Postgres 集成测试:{exc}")

    try:
        yield container.get_connection_url()
    finally:  # pragma: no cover - 容器成功启动时才会走到
        container.stop()


@pytest.fixture()
async def factory(postgres_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_session_factory(postgres_url)
    async with engine.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.engine.dispose()


async def test_tenant_isolation_on_postgres(factory) -> None:
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        await repo.add(Widget(tenant_id="t-1", name="甲的", payload={"n": 1}))
        await repo.add(Widget(tenant_id="t-2", name="乙的"))

    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        assert await repo.count_for_tenant("t-1") == 1
        found = await repo.list_scoped("t-1")
        assert [w.name for w in found] == ["甲的"]
        assert found[0].payload == {"n": 1}  # JSON 列往返


async def test_datetime_keeps_timezone_on_postgres(factory) -> None:
    """Postgres 读回的时间戳带时区(sqlite 会丢,服务里靠比较时刻绕开)。"""
    async with session_scope(factory) as session:
        widget = await WidgetRepository(session).add(
            Widget(tenant_id="t-1", name="带时区")
        )
        assert widget.created_at.tzinfo is not None


async def test_microsecond_ordering_on_postgres(factory) -> None:
    """同秒插入的多行在 Postgres 上顺序稳定(微秒时间戳 + id 兜底)。"""
    async with session_scope(factory) as session:
        repo = WidgetRepository(session)
        for index in range(5):
            await repo.add(Widget(tenant_id="t-order", name=f"w{index}"))

    async with session_scope(factory) as session:
        rows = await WidgetRepository(session).list_scoped(
            "t-order", order_by=[Widget.created_at, Widget.id]
        )
        assert [row.name for row in rows] == [f"w{i}" for i in range(5)]


async def test_require_hides_cross_tenant_on_postgres(factory) -> None:
    from common.errors import NotFoundError

    async with session_scope(factory) as session:
        widget = await WidgetRepository(session).add(
            Widget(tenant_id="t-1", name="甲的")
        )

    async with session_scope(factory) as session:
        with pytest.raises(NotFoundError) as exc_info:
            await WidgetRepository(session).require_for_tenant("t-2", widget.id)
        assert exc_info.value.code == "widget.not_found"


def test_postgres_models_are_declared() -> None:
    """守住:集成测试用的模型与 sqlite 测试共用,避免两套漂移。"""
    assert issubclass(Widget, Base)
    assert Widget.__tablename__ == "widgets"
    assert isinstance(Widget.__table__.c.name.type, String)
