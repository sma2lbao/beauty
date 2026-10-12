"""命名约定是否真正落到元数据上(constraint 名决定迁移可回滚性)。"""

from sqlalchemy import PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class SampleRow(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "sample_rows"

    name: Mapped[str] = mapped_column(String(50))


def test_constraint_names_follow_convention() -> None:
    (pk,) = [
        c
        for c in SampleRow.__table__.constraints
        if isinstance(c, PrimaryKeyConstraint)
    ]
    assert pk.name == "pk_sample_rows"
    # tenant_id 索引名由 ix 模板生成,引用时稳定
    assert "ix_sample_rows_tenant_id" in {
        idx.name for idx in SampleRow.__table__.indexes
    }
