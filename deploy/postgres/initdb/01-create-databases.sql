-- beauty-moon 各服务独立数据库(docs/refactoring/rfc/005-w4.md §R-36)。
--
-- 为什么一服务一库:5 个有迁移的服务(account-service / agent-orchestrator /
-- connector-service / knowledge-service / workspace-service)都用 alembic 默认的
-- alembic_version 表,且 packages/common/common/alembic_kit 未配置 version_table。
-- 若共用一个库,5 份迁移链会互相覆盖 alembic_version,导致 "alembic upgrade head"
-- 静默跳过/重复建表。容器演示因此按服务拆库(生产同样推荐一服务一库)。
--
-- 本脚本只在 postgres 数据卷【首次初始化】时被 docker-entrypoint-initdb.d 执行;
-- 已存在的数据卷不会重跑(重置:docker compose down -v)。POSTGRES_DB=beauty 由
-- compose 创建,是默认连接库;这里再建 5 个业务库。
CREATE DATABASE beauty_account;
CREATE DATABASE beauty_agent;
CREATE DATABASE beauty_connector;
CREATE DATABASE beauty_knowledge;
CREATE DATABASE beauty_workspace;
