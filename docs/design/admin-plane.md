# 管理面 RBAC 与审计（P0-7）

> 状态：P0-7 实施契约（2026-11-10）。范围是**平台运营管理面**：跨账号的账号治理与审计。
> 认证与账号隔离沿用 [architecture.md](../architecture.md) §3/§10；成本归因见
> [cost-attribution.md](cost-attribution.md)，用量上报见 [usage-delivery.md](usage-delivery.md)。

## 1. 目标与非目标

当前 account-service 只有 `/v1/me`：任何一个登录账号都只能看/改自己的资料，没有"平台运营者"
这一层，也没有任何管理动作留痕。P0-7 要把管理面补成**最小可用、可审计**的一层：

1. **角色**：账号有 `user` / `admin` 两种角色，管理端点只对 admin 开放；
2. **账号治理**：管理员能跨账号检索账号、看本月用量/成本、改配额、改角色、停用/启用；
3. **审计**：管理动作（写入 + 越权尝试）落 append-only 审计表，可按操作者/动作/目标检索。

非目标（本批不做，见 §9）：

- 细粒度权限（`scope`/RBAC 策略引擎、按资源授权）——只有 user/admin 两级；
- 跨服务管理面（runs / workspace / 知识库 / outbox 的平台级视图）；
- 审计导出、审计保留期策略、SIEM 投递；
- 管理面独立域名/独立鉴权（仍走 account-service 的 Bearer token）。

## 2. 角色与引导

### 2.1 数据模型

`users` 增加一列：

| 列     | 类型         | 约束                              | 说明                                     |
| ------ | ------------ | --------------------------------- | ---------------------------------------- |
| `role` | `String(16)` | not null, server_default `'user'` | `user` / `admin`；非法值写入时应用层拒绝 |

角色不进入 JWT：`require_admin` 每个请求按 `account_id` 读库，升降权立即生效（access token
仍有效也不影响判定）；代价是每个管理请求多一次主键查询，可接受。

### 2.2 引导（bootstrap）

第一个管理员不能靠管理 API 产生，配置项：

```sh
ACCOUNT__ADMIN_EMAILS='["ops@example.com","owner@example.com"]'
```

规则：

- `ADMIN_EMAILS` 命中（大小写不敏感）的邮箱在**注册/登录**时把 `role` 持久化为 `admin`；
- `require_admin` 同时把"邮箱在名单里"作为兜底：即使数据库里还是 `user`，也按 admin 放行；
- 因此把某人从名单移除不等于立即降权：需先用管理 API 改 `role=user`，再移除名单（或反之，
  文档明确此顺序）。名单本身是部署者可控的引导手段，不是日常授权入口。

### 2.3 停用语义

`users.status` 已有 `active` / `disabled`，登录/刷新/内部签发都已拒绝 disabled（`auth.user_disabled`）。
管理面停用账号时，同一事务内：

- `status = 'disabled'`；
- 吊销该账号全部 `refresh_tokens`（`revoked_at = now`，若模型没有该列则按现有字段实现）；
- 吊销该账号全部 `sessions`（`status = 'revoked'`）。

**残余**：已签发的 access token 在过期前（默认 1h）仍能访问普通端点，因为 JWT 不查库。
管理端点本身会因 `status != active` 被 `require_admin` 拒绝（见 §3.1），所以停用后不能继续做管理动作。

## 3. 后端契约

服务：account-service，前缀 `/v1/admin`（网关经 `/api/account/v1/admin/*` 代理，无需新增网关规则）。

### 3.1 鉴权依赖 `require_admin`

```python
async def require_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
    account_id: str = Depends(get_account_id),
) -> User
```

判定顺序：

1. `get_account_id` 已做 token/临时头解析（401）；
2. 按 `account_id` 取 `User`，不存在 → 403 `admin.required`（不区分"没这账号"）；
3. `user.status != 'active'` → 403 `admin.account_disabled`；
4. `user.role == 'admin'` 或 `user.email in settings.admin_emails` → 放行并返回 `User`；
5. 否则 → **审计 denied 后**抛 403 `admin.required`。

denied 审计用**独立短事务**（`app.state.session_factory` + `session_scope`）写，避免主请求事务
回滚把审计一起回滚；写失败只 `logger.warning`，不改变 403 响应。

### 3.2 响应模型

```text
AdminUsage       { month_tokens:int, month_events:int, month_cost_usd:float, unpriced_events:int }
AdminAccount     { id, email, display_name|null, status, role, quota:object, created_at,
                   usage: AdminUsage }
AdminAccountList { items: AdminAccount[], total:int }
AdminAccountDetail { account: AdminAccount, usage: UsageSummary }   // UsageSummary 见 schemas/insights.py
AuditLogRead     { id, actor_account_id, actor_email, action, target_type, target_id, status,
                   detail:object, request_id|null, ip|null, user_agent|null, created_at }
AuditLogList     { items: AuditLogRead[], total:int }
```

`usage` 按**本自然月**（UTC，与 `/usage/summary` 同口径）聚合 `usage_events`；成本为
`cost_usd` 求和，`None`（未计价）按 0 计入金额但计入 `unpriced_events`。

### 3.3 端点

| 方法  | 路径                                     | 请求                                                             | 响应                 | 审计 action             |
| ----- | ---------------------------------------- | ---------------------------------------------------------------- | -------------------- | ----------------------- |
| GET   | `/v1/admin/accounts`                     | `q?` `status?` `limit=20` `offset=0`                             | `AdminAccountList`   | 不审计（读）            |
| GET   | `/v1/admin/accounts/{account_id}`        | —                                                                | `AdminAccountDetail` | 不审计（读）            |
| PATCH | `/v1/admin/accounts/{account_id}/quota`  | `{monthly_tokens?, max_concurrent_runs?}`                        | `AdminAccount`       | `account.quota.update`  |
| PATCH | `/v1/admin/accounts/{account_id}/role`   | `{role: "user"\|"admin"}`                                        | `AdminAccount`       | `account.role.update`   |
| PATCH | `/v1/admin/accounts/{account_id}/status` | `{status: "active"\|"disabled"}`                                 | `AdminAccount`       | `account.status.update` |
| GET   | `/v1/admin/audit-logs`                   | `limit=20` `offset=0` `actor_account_id?` `action?` `target_id?` | `AuditLogList`       | 不审计（读）            |

约定：

- `q` 对 `email` / `display_name` 做大小写不敏感包含匹配；`status` 仅接受 `active`/`disabled`；
  `limit` 1..100，`offset` >= 0；非法值 422（沿用 FastAPI 校验）。
- 账号不存在 → 404 `account.account_not_found`（沿用仓储 `require` 的既有错误码，与 `/v1/me` 等已发布契约一致）。
- 配额：省略字段 = 保持；显式 `null` = 清除（不限）；整数必须 `>= 1`（`0`/负数 422）。
  只允许 `monthly_tokens` / `max_concurrent_runs` 两个键，其他键原样保留但管理面不暴露编辑。
  请求体必须至少含一个键。
- 改角色：`role` 仅 `user`/`admin`；**不允许改自己的角色**（409 `admin.self_role`），避免唯一管理员自锁。
- 改状态：**不允许停用自己**（409 `admin.self_disable`）。
- 所有写端点返回更新后的 `AdminAccount`（含最新 usage），前端据此就地刷新。
- 审计写在与业务变更**同一事务**（成功提交即留痕；异常回滚则无成功记录，denied 另走独立事务）。

### 3.4 审计表

表名 `audit_logs`，append-only（仓储只提供 `add` / `list` / `count`，不提供 update/delete）：

| 列                 | 类型         | 说明                                                      |
| ------------------ | ------------ | --------------------------------------------------------- |
| `id`               | UUID 主键    | `UUIDMixin`                                               |
| `actor_account_id` | String(32)   | 操作者账号 id，索引                                       |
| `actor_email`      | String(320)  | 冗余快照，账号改邮箱后仍可读旧审计                        |
| `action`           | String(64)   | 见 §3.3；denied 为 `admin.access.denied`，索引            |
| `target_type`      | String(32)   | `account` / `endpoint`                                    |
| `target_id`        | String(64)   | 账号 id 或请求路径，索引                                  |
| `status`           | String(16)   | `success` / `denied`（预留 `failed`）                     |
| `detail`           | JSON         | `{before, after}` 或 `{method, required}`；不含密钥/token |
| `request_id`       | String(64)   | 来自 `common.logging.current_context()`，可串日志         |
| `ip`               | String(64)   | 优先 `X-Forwarded-For` 首段，否则 `request.client.host`   |
| `user_agent`       | String(256)  | 截断                                                      |
| `created_at`       | DateTime(tz) | `TimestampMixin`；列表按 `created_at desc, id desc`       |

审计**不记录**读操作（列表/详情），避免管理台每次刷新都写一行；如后续合规要求再扩。
detail 只写配额/角色/状态前后值，不写 token、密码、BYOK 密钥。

## 4. 前端契约（apps/web）

- `accountApi.me` 的 `Account` 增加 `role: 'user' | 'admin'`。
- 新 `apps/web/src/lib/admin-api.ts`（沿用 `insights-api` 的 base/前缀写法）：
  `listAccounts` / `getAccount` / `updateQuota` / `updateRole` / `updateStatus` / `listAuditLogs`。
- 新页面 `/admin`（`pages/admin.tsx` + `features/admin/*` + `use-admin-model.ts`）：
  - 顶部说明「平台运营 · 跨账号治理，所有写操作留痕」；
  - **账号**页签：搜索框（邮箱/显示名）、状态筛选、分页表格（邮箱/显示名、状态、角色、
    本月 tokens、本月成本、配额、创建时间）；行操作：编辑配额（两个数字输入，空 = 不限）、
    切角色、停用/启用（确认弹窗）；写操作成功后就地更新行并 `invalidate` 审计查询；
  - **审计**页签：按 action / 操作者 id / target_id 过滤，倒序分页表格（时间、操作者、
    action、目标、状态、request_id）；详情用展开行展示 `detail` JSON；
  - 403 / 401 分别给「无管理员权限」/「登录失效」面板，不白屏；
  - 空态、加载态、错误态齐全；沿用 `@beauty/design` 与 `--ha-*` 令牌，不新增依赖。
- 导航：只有 `me.role === 'admin'` 才显示 `/admin` 入口（桌面侧栏「管理」组 + 移动顶栏 +
  命令面板一致）；非管理员直接访问 `/admin` 渲染无权限面板。角色读取复用 `accountApi.me`
  的 react-query 缓存，不新增全局状态。
- 页面源文件 ≤400 行（测试 ≤500），`features/**` 不 import `@/pages`。

## 5. 迁移

新增 alembic revision（account-service）：

1. `users.role`：`String(16)`，nullable=False，server_default `'user'`；已有行自动回填；
2. 建 `audit_logs` 及索引（`actor_account_id`、`action`、`target_id`、`created_at`）。

`uv run alembic check` 必须 clean；服务仍 `AUTO_CREATE_TABLES=false`。

## 6. 配置

| 配置                    | 默认 | 说明                                          |
| ----------------------- | ---- | --------------------------------------------- |
| `ACCOUNT__ADMIN_EMAILS` | `[]` | 引导管理员邮箱列表（JSON 数组，大小写不敏感） |

部署文档（`services/account-service/README.md`）补一段引导说明；`deploy/docker-compose.yml`
不默认写死任何邮箱（避免误开管理面）。

## 7. 验收

后端（`services/account-service/tests/test_admin.py`）：

- 无 token → 401；普通用户 → 403 且落 `admin.access.denied` 审计；admin → 200；
- 引导名单兜底：role=user 但邮箱在名单 → 放行；登录后 role 被持久化为 admin；
- 列表：q 命中 email/display_name、status 过滤、分页 total 正确、usage 聚合含未计价计数；
- 配额：设置/清除/部分字段保留/0 与负数 422/未知账号 404；
- 角色：改他人成功、改自己 409、非法角色 422；
- 停用：status=disabled + refresh/session 全吊销；停用自己 409；disabled 账号访问管理端 403；
- 审计：三个写动作各落一行（含 before/after、actor、request_id）；denied 落独立行；读端点不落；
- 迁移：`alembic upgrade head` 在空库/已有库都能过，`alembic check` clean。

前端（`apps/web/src/pages/admin.test.tsx` + `features/admin/*.test.tsx`）：

- admin 可见入口并渲染账号/审计页签；普通用户看不到入口、直接访问显示无权限；
- 搜索/筛选触发查询参数；配额/角色/状态写操作调对 API 并刷新；
- 审计过滤与空态；错误态不白屏。

门禁：`npx nx lint account-service`、`npx nx test account-service`（cwd=服务目录）、
`npx nx lint @beauty/web`、`npx nx typecheck @beauty/web`、`npx nx test @beauty/web -- --run`。

## 8. 安全与残余

- **权限判定单点**：所有管理端点统一 `require_admin`，不散落 `role ==` 判断；
- **越权可见性**：管理端故意跨账号读取，属预期能力；响应不含密码哈希、BYOK 密文、refresh token；
- **IP 可信性**：`X-Forwarded-For` 仅在可信代理（api-gateway/ingress）后可信，直连暴露时客户端可伪造，
  属残余；后续可改为只信网关注入的 header；
- **审计完整性**：同事务写入保证"改了必留痕"，但没有链式签名/防篡改；DBA 可直接改表，属部署信任边界；
- **读操作不留痕**：管理端列表/详情不审计（§3.4）；
- **角色不细粒度**：admin 拥有全部管理权限；P1-A 工具 policy 引擎落地后再考虑管理面 scope；
- **access token 停用窗**：停用后旧 access token 最长 1h 仍可访问普通端点；
- **引导名单兜底**：从 `ADMIN_EMAILS` 移除前需先改 role，否则名单命中仍放行。

## 9. 后续

- 管理面扩展到 runs/workspace/outbox 的平台视图（需跨服务聚合，优先做只读）；
- 审计导出（CSV/JSON）与保留期清理任务；
- 细粒度 scope（`admin:accounts:read` 等）与 API Key 管理面；
- 管理动作审批（双人复核）与告警（停用/提权事件接 webhook）。
