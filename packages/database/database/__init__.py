"""beauty-moon 数据库共享基座(架构 §7)。

内容:

- `NAMING_CONVENTION`:Alembic/SQLAlchemy 约束命名约定(挂到 `Base.metadata`,
  保证迁移生成的约束名稳定可回滚);
- `database.base`:`Base` + 通用 mixin(`UUIDMixin` 32 位 hex 主键、
  `TimestampMixin` 微秒级时间戳、`TenantMixin` 租户隔离);
- `database.session`:async engine / session 工厂 / `session_scope` 事务边界;
- `database.repository`:`Repository`(无租户语义)与 `TenantRepository`
  (自动租户过滤 + 统一 404 语义);
- `database.uow`:`UnitOfWork`——显式事务 + 跨仓储同一 session(脚本/worker/测试);
- `tests/test_postgres_integration.py`:Postgres 集成(优先 `TEST_POSTGRES_URL`,
  其次 testcontainers,都不可用则跳过)。
"""

# Alembic/SQLAlchemy 约束命名约定,保证迁移生成的约束名稳定可回滚。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(referred_table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}
