# database

beauty-moon 数据库共享基座。架构定位见 `docs/architecture.md`(§7 数据模型要点)。

| 模块 | 内容 |
| --- | --- |
| `database.NAMING_CONVENTION` | 约束命名约定(挂到 `Base.metadata`,迁移可回滚的前提) |
| `database.base` | `Base` + `UUIDMixin`(32 位 hex 主键)/ `TimestampMixin`(微秒时间戳)/ `TenantMixin`(tenant_id) |
| `database.session` | `create_session_factory` / `session_scope`(成功提交、异常回滚、总是关闭) |
| `database.repository` | `Repository` / `TenantRepository`:租户过滤 + 统一 404 语义 |
| `database.uow` | `UnitOfWork`:显式事务边界 + 跨仓储共享 session |

## 仓储(数据访问的唯一入口)

```python
class RunRepository(TenantRepository[Run]):
    model = Run
    not_found_code = "run.not_found"
    not_found_message = "run 不存在"
    id_detail_key = "run_id"      # 404 details 的主键键名(保持各服务既有契约)

run = await RunRepository(session).require_for_tenant(tenant_id, run_id)
```

约定:

- **租户隔离是下限,不是礼貌**:`TenantRepository` 的所有读取自动带 `tenant_id`;
  绑定漏了 `TenantMixin` 的表会在**类定义时**直接 `TypeError`(见
  `tests/test_repository.py::test_tenant_repository_rejects_model_without_tenant_mixin`);
- **跨租户一律 404,不是 403**:不泄漏"存在但无权"这一信息;
- **仓储不提交**:事务边界只由 `session_scope`(HTTP 一请求一事务)或
  `UnitOfWork`(脚本/worker/测试)负责——这样"一个事务里跨多个仓储"才成立;
- `select()` / `scoped()` 是复杂查询的逃生口,但仍要求带租户条件;
- 复杂关联查询(如 knowledge-service 的分块 join)可继续用 SQLAlchemy 直接写,
  逐步收敛,不阻塞。

## 工作单元

```python
async with UnitOfWork(factory, repositories=(UserRepository,)) as uow:
    await uow.repo(UserRepository).add(User(...))
# 正常退出提交;抛异常回滚后再抛出(不吞错)
```

## 测试

`npx nx test database` 走 sqlite,零外部依赖。Postgres 集成用例需要 Docker 或现成实例:

```sh
# 方式一:Docker(自动起 postgres:16-alpine)
npx nx test database

# 方式二:现成实例(如 deploy/docker-compose.yml 起的 postgres)
TEST_POSTGRES_URL=postgresql+asyncpg://beauty:beauty@localhost:5432/beauty npx nx test database
```

两者都不可用时该文件**整体 skip 并打印原因**(不是静默通过);它覆盖 sqlite
验不了的两点:时间戳带时区、同秒多行的排序稳定。

## 迁移进度

已收敛:account-service、workspace-service。待收敛:agent-orchestrator
(事件溯源查询较多)、knowledge-service(分块 join)、connector-service。
