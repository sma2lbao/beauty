"""session 工厂与事务边界(aiosqlite 起真实异步引擎验证)。"""

from datetime import datetime

from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin
from database.session import create_session_factory, session_scope


class ProbeRow(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "probe_rows"

    name: Mapped[str] = mapped_column(String(50))


async def test_scope_commit_and_query(tmp_path) -> None:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_scope(factory) as session:
        session.add(ProbeRow(tenant_id="t-1", name="a"))

    async with session_scope(factory) as session:
        row = (await session.scalars(select(ProbeRow))).one()
        assert row.tenant_id == "t-1"
        assert len(row.id) == 32
        assert isinstance(row.created_at, datetime)

    await factory.engine.dispose()


async def test_scope_rollback_on_error(tmp_path) -> None:
    factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with factory.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class Boom(Exception):
        pass

    try:
        async with session_scope(factory) as session:
            session.add(ProbeRow(tenant_id="t-1", name="bad"))
            raise Boom
    except Boom:
        pass

    async with session_scope(factory) as session:
        assert (await session.scalars(select(ProbeRow))).all() == []

    await factory.engine.dispose()
