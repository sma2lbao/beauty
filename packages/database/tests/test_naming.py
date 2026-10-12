import database


def test_naming_convention_covers_all_constraint_kinds() -> None:
    """五类约束都必须有稳定命名模板,缺一会导致 alembic 迁移不可回滚。"""
    assert set(database.NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}
    # 除索引(以列标签命名)外,其余模板都以表名为锚,保证约束名随表稳定
    for kind, template in database.NAMING_CONVENTION.items():
        if kind != "ix":
            assert "%(table_name)s" in template
