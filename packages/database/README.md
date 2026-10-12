# database

beauty-moon 数据库共享基座。架构定位见 `docs/architecture.md`。

当前(骨架阶段)内容:

- `database.NAMING_CONVENTION`:Alembic/SQLAlchemy 表约束命名约定(依赖 sqlalchemy 后直接挂到 DeclarativeBase.metadata)

随实现逐步补齐(sqlalchemy/alembic 随用随引,不提前声明):DeclarativeBase + 通用 mixin(UUID 主键/时间戳/tenant_id)、async engine 与 AsyncSession 工厂、泛型 Repository + Unit of Work、testcontainers 测试夹具。

命令(仓库根执行):`npx nx lint database` / `npx nx format database` / `npx nx test database` / `npx nx sync database`
