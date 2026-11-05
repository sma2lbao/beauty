# 多租户拆除：tenant → account（ChatGPT 个人模型）

> 状态：定稿（2026-11-05）。0.1.0 未发布，允许破坏式改造；不保留旧列、不双写、不做数据迁移映射。

## 1. 目标模型

- 一个部署 = 一个服务实例，可容纳多个账号；**账号 = `users` 行，`account_id` = `users.id`（32 位 hex）**。
- 不存在组织（Organization）、成员、邀请、角色、套餐。原「租户内共享」一律变为「账号私有」。
- 账号之间数据互不可见，靠仓储强制过滤 + 跨账号 404（不泄漏存在性）。
- ChatGPT 个人语义：每个账号只看得到自己的 workspace / 会话 / 交付物 / 知识 / 连接器 / BYOK / API Key / 用量。

## 2. 命名约定（硬性，服务必须严格一致）

| 旧                                                                                              | 新                                               |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `TenantMixin`                                                                                   | `AccountMixin`                                   |
| `tenant_id`（列/字段/变量）                                                                     | `account_id`                                     |
| `TenantRepository`                                                                              | `AccountRepository`                              |
| `scoped/require_for_tenant/get_for_tenant/count_for_tenant/exists_for_tenant/delete_for_tenant` | 对应 `*_for_account` / `scoped(account_id, ...)` |
| `JwtVerifier(tenant_claim="tenant_id")`                                                         | `account_claim="account_id"`                     |
| `Principal.tenant_id`                                                                           | `Principal.account_id`                           |
| `X-Tenant-Id`                                                                                   | `X-Account-Id`                                   |
| `get_tenant_id` / `get_internal_tenant_id`                                                      | `get_account_id` / `get_internal_account_id`     |
| `bind_request_context(tenant_id=...)`                                                           | `bind_request_context(account_id=...)`           |
| `EventEnvelope.tenant_id`                                                                       | `account_id`                                     |
| `OutboxEvent.tenant_id` / `envelope_tenant_id`                                                  | `account_id` / `envelope_account_id`             |
| `mark_processed(tenant_id=...)`                                                                 | `mark_processed(account_id=...)`                 |
| `TokenBroker.token(tenant_id)`                                                                  | `token(account_id)`                              |
| `auth.tenant_missing` / `auth.header_missing` 文案里的租户                                      | `auth.account_missing`                           |
| 中文文案「租户 / 组织 / 成员」                                                                  | 「账号」（部署边界保留「部署」）                 |

## 3. 不变量

1. 每张账号私有表必须混入 `AccountMixin`；只允许用 `AccountRepository`（`Repository` 仅用于无归属全局表，如 `oauth_clients`）。
2. `account_id` 只能来自 JWT claim（`AUTH_ENABLED=true`）或 `X-Account-Id`（仅在 `AUTH_ENABLED=false` 的临时/内部路径）。不做请求头回退。
3. 跨账号访问一律 404，不返回 403。
4. 事件信封必带 `account_id`（平台级事件可为 `None`）。
5. 不引入 role / permission / membership 概念。

## 4. account-service 契约

- 删除模型 `Organization` 与表 `organizations`。
- `User` 变为账号：去掉 `tenant_id`；`UNIQUE(subject)`（`subject` 自建认证下 = email）；保留 `email/display_name/status/password_hash`；**删除 `role`**；新增 `quota: JSON default {}`（承接原 `Organization.quota`）。
- 端点变更：
  - 删除 `GET/PATCH /v1/organization`、`POST/GET /v1/users`、`GET/PATCH /v1/users/{user_id}`。
  - 新增 `GET /v1/me`、`PATCH /v1/me`（只改 `display_name`），返回 `AccountRead{id,email,display_name,status,created_at}`。
  - `POST /v1/auth/register`：请求体不变（email/password/display_name），只建账号、不建组织；响应沿用扁平 `TokenResponse`：`access_token, refresh_token, token_type, expires_in, account_id, subject, session_id`（删除 `tenant_id/organization_id/user_id/role`）。
  - `POST /v1/auth/login`：同上（按 email 全局查账号），同一 `TokenResponse`。`refresh` / `logout` 路径与请求体不变（内部改用 account_id）。
  - `POST /v1/auth/issue`：请求体 `{"account_id": str, "subject": str}`，响应 `IssueResponse{access_token, token_type, expires_in, account_id, subject}`；账号不存在或非 active 返回 401 `auth.account_missing`。
  - `sessions / api-keys / byok / usage / oauth` 路径不变，作用域改账号；内部端点用 `X-Account-Id`（`get_internal_account_id`）。
  - `GET /v1/usage/quota` 改读 `users.quota`；`plan` 概念删除。新账号默认 quota 为空（= 不限，保持现有 fail-open 语义）。
- JWT access token claim：`account_id`（+ `sub/iss/aud/exp`）。refresh token 记录 `account_id`。
- 迁移（新增 1 个 revision）：删 `organizations` 表；`users` 去 `tenant_id`、删 `uq_users_tenant_id`、建 `UNIQUE(subject)`、加 `quota` 列、删 `role` 列；其余表 `tenant_id` 重命名为 `account_id`。

## 5. 其他服务（机械改动 + 服务特有）

每个服务：模型 mixin/列改名 → 仓储类与方法 → `api/deps.py`（`get_account_id`）→ 服务层参数/变量 → `TokenBroker.token(account_id)` → 内部头 → 1 个 alembic 迁移 → 测试与 README。

- `agent-orchestrator`：runs/conversations/mcp/skills/approval/memory/outbox 全部 `account_id`；`run_quota` 用 `account_id` 取快照；用量上报与事件用 `account_id`。
- `workspace-service`：Workspace/WorkspaceArtifact/Sandbox 改 `account_id`；默认工作区按账号查找；`UNIQUE(account_id, name)`。
- `knowledge-service`：文档/分块/嵌入改 `account_id`；检索过滤跟着改。
- `connector-service`：连接/凭证/租户应用改 `account_id`；`tenant_apps` 表 → `account_apps`（模型 `AccountApp`，迁移 `op.rename_table`），唯一约束 `(account_id, provider)`。
- `tool-service`：tool_calls/file_reads 改 `account_id`；审计与查询跟着改。
- `llm-gateway`：BYOK 目录/密钥读取、用量上报、内部头改 `account_id`。
- `api-gateway`：Cookie 会话 payload `user.account_id`（删 `tenant_id/organization_id`）；`schemas/auth.py`、限流键、代理头改 `X-Account-Id`；`services/account.py` 的 issue 请求体改 `account_id`。
- `apps/desktop` 本地模式无租户概念，不改。

## 6. Alembic 迁移规范

- 每个有迁移的服务新增 1 个 revision（`uv run alembic revision -m "tenancy to account"`），**只重命名列、不删数据**；旧行的 `tenant_id` 值会留在 `account_id` 里成为不可见数据，本地执行 `npm run dev:reset` 重建，文档里注明。
- 列重命名 + 索引 + 唯一约束的统一写法（SQLite 用 batch 模式，Postgres 兼容）：

```python
def upgrade() -> None:
    op.drop_index('ix_runs_tenant_id', table_name='runs')
    with op.batch_alter_table('runs') as batch:
        batch.drop_constraint('uq_runs_tenant_id', type_='unique')
        batch.alter_column('tenant_id', new_column_name='account_id')
    op.create_index('ix_runs_account_id', 'runs', ['account_id'])
    op.create_unique_constraint('uq_runs_account_id', 'runs', ['account_id', 'tool_call_id'])
```

- 约束/索引名以 `packages/database/database/NAMING_CONVENTION` 与既有迁移为准；不确定时先用 `alembic check` / 反射确认，**不要猜**。
- 验收：服务目录内 `uv run alembic upgrade head` + `uv run alembic check`（可用临时 sqlite 的 `DATABASE_URL`）。
- `packages/database`/`common`/`events` 无迁移。

## 7. 前端（apps/web）

- `src/lib/account-api.ts`：删除 `Organization`/`Member`/`Role` 类型与成员/邀请函数；新增 `Account`、`getMe()`、`updateMe()`（`/v1/me`）；sessions/api-keys/byok/oauth-apps/usage 保留。
- `src/features/settings/`：删 `organization-card.tsx`、`invite-member-card.tsx`、`members-card.tsx`；新增「账号」卡片（邮箱/显示名/状态）；保留 sessions / api-keys / byok / oauth-apps；`use-settings-model.ts` 同步。
- `src/pages/settings.tsx` 标题与布局；`src/lib/query-keys.ts` 的 organization key 删除。
- 登录/注册页文案：「创建账号」；不再出现「组织/团队/成员/邀请/套餐」。
- workspace 相关文案「团队的资料空间」→「你的资料空间」。
- 所有相关测试更新；`npx nx typecheck @beauty/web` + `test -- --run` + `lint` 全绿。

## 8. 文档 / 脚本 / 部署

- `docs/architecture.md`：§10 鉴权与多租户 → 「部署边界与账号」，删除组织/成员/角色/套餐叙述。
- `AGENTS.md`：关键机制里「多租户业务表必须混入 TenantMixin」→「账号私有表必须混入 AccountMixin」；项目结构里 account-service 描述去掉组织/成员。
- `docs/local-dev.md`、`services/*/README.md`、`packages/*/README.md`、`deploy/docker-compose.yml` 中的租户措辞/环境变量。
- `scripts/golden-run/golden_run.py` 的 tenant 引用；`scripts/seed-local.sh` 走注册接口，不改。

## 9. 验收

- Python 项目：`npx nx lint <p>` + `npx nx format <p>` + `npx nx test <p>`；每个有迁移的服务再跑 `uv run alembic check`。
- TS：`npx nx typecheck @beauty/web` + `npx nx test @beauty/web -- --run` + `npx nx lint @beauty/web`。
- 全局：`npx nx run-many -t lint test`、`bash scripts/refactor-gate.sh --affected`、`bash scripts/golden-run/golden-run.sh`、`npm run dev:reset && npm run dev:migrate && npm run dev:seed`。
- 覆盖率阈值不得下调（Python 11 个项目 + 根 vitest.config.mts）。
- `grep -rn "tenant" --include=*.py --include=*.ts --include=*.tsx services packages apps/web apps/desktop` 结果应为 0（历史文档/迁移文件内的叙述除外）。

## 10. 明确不做

- `apps/desktop` 本地模式（无租户）。
- `oauth_clients` 等全局表。
- 角色/权限/邀请/成员/套餐/计划。
- 不改覆盖率门禁数字；不调 `.env`；不手改 lock 文件。
