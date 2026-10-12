"""beauty-moon 数据库共享基座(骨架阶段)。

计划内容(依赖 sqlalchemy/alembic,随实现引入,不提前声明):

- DeclarativeBase + 命名约定/通用 mixin(UUID 主键、时间戳、tenant_id);
- async engine 与 AsyncSession 工厂;
- 泛型 Repository + Unit of Work;
- testcontainers 测试夹具。
"""

# Alembic/SQLAlchemy 约束命名约定,保证迁移生成的约束名稳定可回滚。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}
