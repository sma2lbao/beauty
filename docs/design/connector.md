# 连接器平台（Connector Platform）设计

> 状态：P1 平台骨架已实现（P2 飞书 provider 已于 2026-11-04 按新规划移除） · 2026-11-03 定稿 / 2026-11-04 回填实现状态
> 关联：[architecture.md](../architecture.md) §8.2（MCP client）、§11.2（审批）、[tool-service.md](./tool-service.md)、[01-workbuddy.md](../research/connector/01-workbuddy.md)、[02-mainstream.md](../research/connector/02-mainstream.md)、[03-feishu-notion.md](../research/connector/03-feishu-notion.md)
> 定位：本文是「连接器」这一能力域的单一真源，回答**做什么、照谁做、先做哪个、按什么顺序做**；服务边界仍以 [architecture.md](../architecture.md) 为准。
> 现状：P1 平台已落地——授权会话、连接 CRUD/撤销、刷新单飞 + 24h 巡检、outbox 生命周期事件、tool-service 执行面与 `/connectors` 页面；P2 飞书 provider 已按 2026-11-04 规划移除；P3/P4 未开始。
> **2026-11-04 规划变更**：按新规划移除内置飞书 provider（`providers/feishu.py`、飞书配置项与飞书专属测试）；平台骨架/页面/执行面/审计保留；后续 provider 待新规划。

## 1. 摘要

一句话：把 `connector-service`（:8004，现为「有表无入口」的空框架）升级为**连接器平台**——统一承载 OAuth 授权、凭证托管、token 刷新、工具暴露与执行代理；连接器本体用**代码 manifest** 声明，第一个 provider 是**飞书（只读）**，第二个预留 **Notion**。

三条主线（也是本稿与「直接接 MCP」的分野）：

1. **连的是 SaaS，不是协议**。飞书/Notion 都需要「用户以自己身份授权、平台代其读写」，这是 OAuth 连接生命周期问题，MCP 只是可选的暴露层；先有连接平台，MCP 才能成为它的一个 provider 来源。
2. **凭证零出口**。第三方 token 只存在 connector-service 的加密列里；agent、tool-service、前端都拿不到 token，只能发「工具名 + 参数」。这条对齐 WorkBuddy 的 `server-side` 档与 Composio/Arcade 的做法。
3. **能力即工具**。每个 provider 能力编译成现有 `ToolDescriptor`，复用 `auto|confirm|deny` 审批与 `run_events` 审计；不为连接器另造一套权限与追踪。

### 1.1 关键决策（D1–D13）

| #   | 决策                                                                                                                                         | 理由 / 依据                                                                                                                               |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | **connector-service 做平台层**；每 provider 一个模块（`connector/providers/feishu.py`），能力用**代码 manifest** 声明，不引入 YAML / builder | 定义层与连接层分离（Composio AuthConfig vs ConnectedAccount）；Nango 已废弃 `nango.yaml`（带 YAML 的部署被直接拒绝），是最有力的反向证据  |
| D2  | 连接**归属到授权用户**（`owner_subject`），组织内按权限可见；组织共享连接（无 owner）留到 P2                                                 | 撤销与审计必须能回答「谁授权的」；现状 `connections` 无归属字段是明确缺口                                                                 |
| D3  | 授权走**服务端一次性授权会话**：`oauth_sessions` 存 state nonce / PKCE verifier / tenant / user / redirect / 过期，回调用 nonce 换回身份     | state 只放随机数、身份靠服务端行查回；RFC 9700 强制 PKCE(S256)、精确 redirect、禁隐式流                                                   |
| D4  | **凭证零出口**：agent 侧新增 `/v1/connections/{id}/execute`，由 connector-service 解密并注入 token；token 不下发给 tool-service 或前端       | WorkBuddy `server-side`、Composio Proxy Execute、Arcade token vault 同构；解密点收敛到一处，泄漏面可控                                    |
| D5  | **刷新单飞**：按连接行加数据库行锁 + 二次检查 + 同事务写回；到期前 5 分钟懒刷新 + 每 24h 巡检保活；失败置 `expired` 并发 outbox 事件         | 飞书 `refresh_token` 一次性、旧的立即失效，多副本用进程锁会互相覆盖；Nango 24h 保活可防 provider 回收闲置 token                           |
| D6  | 能力编译为 `ToolDescriptor`，命名 **`Feishu__read_document`**（provider 前缀 `Feishu__`，与 `mcp__` 风格区分）；执行面走 tool-service        | 复用 catalog/审批/审计，不新建权限体系；`mcp__` 是外部协议命名，连接器是自有能力，用可读前缀更利于模型选择                                |
| D7  | **错误语义分层**：`connector.auth_expired` 与 `connector.upstream_*` 分开；只有 auth/scope 类错误触发前端「重新连接」                        | WorkBuddy 明确只有授权失效才提示重连，超时/无权限不提示；否则所有 4xx/5xx 都会误导用户                                                    |
| D8  | **只做 on-demand 拉取**：v1 不做同步引擎、不做事件订阅、不做后台定时抓取                                                                     | WorkBuddy 官方「不主动定时抓取」；省掉同步状态机/游标/去重一整块；与架构现状一致                                                          |
| D9  | **飞书 v1 写死企业自建应用 + 用户身份（`user_access_token`）+ 只读能力**；应用凭证全局配置 + `tenant_apps` 表按租户覆盖                      | 长连接仅自建应用支持、商店应用需 ISV 资质与上架审核；用户身份才符合「谁授权以谁身份读文档」；写 scope 要重新发版 + 管理员审批，边际收益低 |
| D10 | **Notion 放 P2**，同平台加 provider；`Notion-Version` 做成配置项，token TTL 按未知处理（401 触发刷新 + introspect 巡检）                     | Notion 一个 token 绑「一个 workspace + 一个用户」，唯一键要含 workspace；官方未给 `expires_in`                                            |
| D11 | **MCP 自定义连接器留到 P2**：把已有 MCP server 也挂进「连接器」UI，复用同一审批/审计，凭证仍由 orchestrator 的 `mcp_oauth` 管                | v1 不混两种凭证模型（自有加密列 vs `mcp_server_auth`）；先让原生 provider 跑通，避免一次改两套生命周期                                    |
| D12 | 所有连接器调用**必须经 tool-service**（审计唯一落 `tool_calls`）；connector-service 不建第二张调用审计表                                     | 对齐 tool-service 设计 D8「审计单源」；connector-service 只记结构化日志与 usage 指标                                                      |
| D13 | 写类工具（创建文档/追加块）**默认 risk=WRITE + 审批 confirm**；v1 飞书不暴露任何写工具                                                       | 写错文档不可逆、用户信任成本高；飞书写能力需要重新发版权限，放到 P1 再评估                                                                |

### 1.2 分期

| 期  | 内容                                                                     | 可回滚性                        | 状态   |
| --- | ------------------------------------------------------------------------ | ------------------------------- | ------ |
| P0  | 决策确认（§9.1）+ 迁表设计                                               | —                               | 已定稿 |
| P1  | 平台骨架：授权会话 / 连接创建 / 刷新 / 撤销 / 执行代理 / provider 注册表 | `CONNECTOR__PROVIDERS_ENABLED=` | 已实现 |
| P2  | 飞书 provider（只读 P0 工具）+ 前端 `/connectors` + tool-service 执行面  | 同上（按 provider 关）          | 已实现 |
| P3  | 飞书写工具（confirm）+ 多维表格/表格读 + 组织共享连接                    | 按工具关闭                      | 未开始 |
| P4  | Notion provider + MCP 自定义连接器并入                                   | 按 provider 关                  | 未开始 |

## 2. 实现前现状盘点（2026-11-03，保留作背景）

> 本节是初稿时的缺口清单；P1/P2 已按 §8 清单补齐，落地情况见 §8 实现状态与 §5.3/§5.5 的实现边界。

| 模块                 | 现状                                                                                                              | 与目标差距                                                        |
| -------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `connector-service`  | 仅 `GET /v1/connections`、`GET /v1/connections/{id}`、`DELETE /v1/connections/{id}`；无创建连接、无 OAuth、无刷新 | 缺授权会话、provider 注册表、执行代理、内部服务鉴权               |
| `connections` 表     | `provider/account/display_name/status/credentials(JSON)` + 租户唯一约束；凭证 Fernet 信封加密                     | 缺 `owner_subject`、`scopes`、`expires_at`、`enabled`、`metadata` |
| `api-gateway`        | `/api/connector/*` → `:8004/v1/*` 已接线                                                                          | 无需改动（回调 URL 直接复用该前缀）                               |
| `tool-service`       | catalog 代码真源 + 执行器(`SANDBOX`/`HTTP`/`NONE`) + 审批 + `tool_calls` 审计                                     | 缺 `ExecutorKind.CONNECTOR` 与按租户装配连接器工具                |
| `agent-orchestrator` | 完整 MCP client（DCR/PKCE/刷新/stdio/审计）                                                                       | 连接器工具需进入工具装配与事件流                                  |
| `apps/web`           | 无 `/connectors` 页面、无侧栏入口                                                                                 | 需新增连接器页面与授权回流                                        |
| 文档/门禁            | `docs/README.md` 索引 + `check-docs.sh`/`check-workspace.sh`                                                      | 本文与三份调研需入索引                                            |

## 3. 调研结论

### 3.1 WorkBuddy 怎么做

详见 [01-workbuddy.md](../research/connector/01-workbuddy.md)。要点：

- **连接器 = 协议 + 能力说明（Skill）+ 授权**三件套；接法 `MCP+Skill` 或 `CLI+Skill` 二选一；`auth_mode` 仅三档：无认证 / `server-side`（云端 OAuth，端侧零 token）/ `token`（用户填表，存本机）。
- **UX 最值得抄**：任务执行中按需弹授权卡 → 授权后自动续跑；授权失效只**单步自动重试一次**；「关闭启用」≠「解绑」。
- **权限边界**：每连接器独立授权、不主动定时抓取、不超出用户已有权限；写操作需明确指令；连接器读写不扣积分。
- **不抄**：CLI 形态与托管运行时（成本高）、第三方市场/版本闸门（v1 无第三方接入）、`@` 引用（官方连接器文档中不存在）。

### 3.2 主流工程做法

详见 [02-mainstream.md](../research/connector/02-mainstream.md)。三类物种别混着抄：

| 物种         | 代表                                                | 本仓库是否需要                                 |
| ------------ | --------------------------------------------------- | ---------------------------------------------- |
| 授权基础设施 | Nango、Composio、Arcade、Paragon、Pipedream、Zapier | **要**：只抄连接生命周期与凭证托管             |
| 统一数据层   | Merge、Airbyte                                      | 不要：归一化 schema 的成本远高于当前需求       |
| 协议层       | MCP                                                 | **部分**：作为 P2 的扩展来源，不作为 v1 主路径 |

五条可执行范式：

1. 后端 mint 短令牌 + 服务端一次性授权会话（state 只放 nonce）。
2. 凭证零出口：Proxy Execute / token vault，token 不进入模型上下文。
3. 刷新单飞 + 保活巡检；失败事件化（outbox），不静默丢。
4. 能力 = 工具，复用审批与审计。
5. 定义层用代码（TS/Zod 或 dataclass），不用运行时可编辑的 YAML/builder。

### 3.3 飞书与 Notion 的关键差异

详见 [03-feishu-notion.md](../research/connector/03-feishu-notion.md)。

| 维度      | 飞书                                                                | Notion                                                                  | 对实现的影响                                           |
| --------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------ |
| 应用模型  | 自建应用（每客户一套凭证）/ ISV 商店应用                            | 一个 public integration 服务所有 workspace                              | 飞书应用凭证可 per-tenant 覆盖，Notion 全局配置        |
| 连接粒度  | 租户应用凭证 + N 个用户授权                                         | **一个 token = 一个 workspace + 一个用户**                              | 唯一键要含 `workspace_id` 与 `owner_subject`           |
| token     | `tenant_access_token`（应用）+ `user_access_token`（用户，2h 左右） | 单一 bearer（OAuth），**无 `expires_in`**                               | 飞书两套缓存/刷新；Notion 按 401 触发刷新 + introspect |
| 刷新语义  | refresh **一次性**、旧值立即失效、365 天强制重授权                  | 返回新 access + 新 refresh                                              | 都要按连接行串行；飞书还要处理 365 天硬到期            |
| 权限模型  | scope（后台申请 + 管理员审批）+ 数据权限（行/列）                   | capabilities（4 类 × 3 档用户信息）+ 页面级授权                         | 连接详情要展示「已授予 vs 期望」，并提示重新授权       |
| 分页/限流 | `page_token`；429 + `x-ogw-ratelimit-reset`（部分旧接口 400）       | `start_cursor`；429 + `Retry-After`，connection 级 + workspace 共享预算 | 不做通用分页/限流基类，只共用连接与凭证基座            |
| webhook   | 至少一次；3 秒超时；长连接仅自建应用                                | **至多一次**；指数退避 ×8 至 24h；事件只是「变了」                      | v1 都不做（D8）；Notion 若做必须 reconcile 兜底        |

## 4. 方案比选

| 方案                    | 做法                                                                                          | 优点                                             | 缺点                                                                                                 |
| ----------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| A. MCP-first            | 把飞书/Notion 的 MCP server 装进现有 orchestrator MCP client，零新代码                        | 最快出 demo；复用 DCR/PKCE/stdio                 | 飞书/Notion 无官方 MCP；凭证在 MCP 进程里，租户隔离/审批/审计归不到连接对象；消费者授权没有 DCR 场景 |
| B. 纯原生适配器         | 在 connector-service 内为每个 provider 写 OAuth + 工具 + 执行                                 | 完全可控；凭证零出口；审计统一                   | 每个 provider 重复写 OAuth/刷新/分页/限流                                                            |
| C. 平台 + 原生 provider | connector-service 做连接平台；飞书/Notion 用代码 manifest 声明 provider；MCP 作为 P2 扩展来源 | 新 provider 只写 provider 模块；平台能力只做一次 | 首版要建平台骨架（授权会话/刷新/执行代理），约多 5 人日                                              |

**推荐 C**。理由：飞书/Notion 的差异集中在 provider 层（token 形态、分页、限流、权限模型），平台层（连接生命周期、凭证加密、执行代理、错误分层）完全可以共用；若走 A，将来做「组织共享连接 / 撤销审计 / 按工具审批」时仍要补回 B 的全部工作，等于做两遍。

**边界**：C 不是「什么都自建」——不抄 Unified API 归一化、不做同步引擎、不外包 SaaS 凭证托管、不引入 MCP 的 DCR/PRM 全套给内部 agent。

## 5. 目标架构

### 5.1 组件与数据流

```text
浏览器 /connectors
   │  POST /api/connector/v1/connectors/feishu/authorize
   ▼
api-gateway ──▶ connector-service ── 建 oauth_sessions(state nonce + PKCE verifier, 10min)
   │                                   │
   │◀────── { authorize_url } ─────────┘
   │ 302 飞书授权页 ── 用户同意 ──▶ 飞书回调
   │                              /api/connector/v1/oauth/feishu/callback?code&state
   │                                   │ 校验 nonce 单次消费 → code 换 token
   │                                   │ 加密落库 connections → 302 回
   │                                   │ /connectors?provider=feishu&status=connected&connection_id=…
   ▼
agent-orchestrator ── tool-service ── POST /v1/connectors/{provider}/execute {tool, args, run_id}
                                          │ 解密注入 token + 限流 + 错误映射
                                          ▼
                                     飞书 OpenAPI
```

要点：浏览器只经手 `authorize_url` 与回调重定向；agent 只经手工具名与参数；token 只在 connector-service 内存中出现。

### 5.2 数据模型

**connections 扩展**（迁移 `8f1c2a5d7b90`，`down_revision = 'b2e4c7a91d33'`）：

| 列                          | 类型                  | 说明                                                       |
| --------------------------- | --------------------- | ---------------------------------------------------------- |
| `owner_subject`             | String(64)            | 授权用户的 subject（JWT `sub`）；组织共享连接为空，P3 再补 |
| `auth_kind`                 | String(16)            | `user` / `tenant`；飞书 v1 固定 `user`                     |
| `provider_account_id`       | String(128)           | 飞书 `open_id` / Notion `bot_id`                           |
| `external_workspace`        | String(128)           | 飞书 `tenant_key` / Notion `workspace_id`                  |
| `scopes`                    | JSON                  | 实际授予的 scope 列表                                      |
| `metadata`                  | JSON                  | provider 附加信息（workspace_name、user_name、app_id 等）  |
| `enabled`                   | Boolean               | 「关闭启用」≠「解绑」；默认 true                           |
| `expires_at`                | DateTime(tz)          | access token 过期时间；Notion 未知时为空                   |
| `last_refreshed_at`         | DateTime(tz)          | 最近一次成功刷新                                           |
| `last_error_code`           | String(64)            | 最近一次失败码，用于前端展示                               |
| `revoked_at` / `revoked_by` | DateTime / String(64) | 撤销留痕                                                   |

保留现有唯一约束 `(tenant_id, provider, account)`；`account` 写稳定可读标识（飞书 `open_id`，Notion `workspace_id:owner_subject`），并在 provider 内校验，避免同账号重复授权。

**oauth_sessions**（新表）：`id`(32 hex)、`tenant_id`、`owner_subject`、`provider`、`state_nonce`(unique, 64)、`code_verifier`(nullable)、`redirect_uri`、`scopes`、`status`(`pending|completed|failed|expired`)、`error_code`、`connection_id`、`expires_at`、`created_at`、`completed_at`。**回调成功/token 落库后再置 completed**；重复 nonce 一律 400。

**tenant_apps**（新表，可选覆盖）：`tenant_id`、`provider`、`app_id`、`app_secret_encrypted`(JSON)、`config`(JSON)，唯一 `(tenant_id, provider)`。解析顺序：租户行 → 全局 Settings，实现在 `connector/services/apps.py`（`app_id` 与 `app_secret` 都可用才生效，半配置行记 warning 后回退全局）；授权、刷新、巡检与执行面均按租户解析 provider，目录的 `configured` 仍用全局配置、不暴露租户凭证。v1 不做管理 UI（先配置/脚本写入）。

**outbox_events / processed_events**（迁移 `c4f7b1e93a52`，`down_revision = '8f1c2a5d7b90'`）：与 `events.outbox` 同源，存连接生命周期事件（`created/expired/revoked`）供至少一次投递；`processed_events` 供消费方按事件 id 幂等。`CONNECTOR__OUTBOX_ENABLED=false` 时不写表、只落结构化日志。

### 5.3 API 契约（connector-service）

| 方法/路径                                  | 说明                                                                                                                         | 鉴权                                  |
| ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `GET /v1/connectors`                       | provider 目录：名称、`auth_kind`、所需 scope、工具清单（含 risk）、是否已配置                                                | 用户 JWT                              |
| `POST /v1/connectors/{provider}/authorize` | 建授权会话，返回 `{provider, authorize_url, expires_at}`（session 只在服务端）                                               | 用户 JWT                              |
| `GET /v1/oauth/{provider}/callback`        | 校验 state → 换 token → 落库 → 302 回 `/connectors?provider=&status=connected&connection_id=`（失败带 `status=error&code=`） | 无（一次性 nonce）                    |
| `GET /v1/connections`                      | 列表，可按 `provider`/`status`/`enabled` 过滤；永不返回 credentials                                                          | 用户 JWT                              |
| `GET /v1/connections/{id}`                 | 详情：scopes 已授予/期望、工具、`expires_at`、`last_error_code`                                                              | 用户 JWT                              |
| `PATCH /v1/connections/{id}`               | `{display_name?, enabled?}`                                                                                                  | 用户 JWT + owner                      |
| `POST /v1/connections/{id}/refresh`        | 手动刷新（单飞）；成功返回连接；失败按错误码映射 409/429/504/502（§5.7）                                                     | 用户 JWT + owner                      |
| `POST /v1/connections/{id}/check`          | 连通性探测（飞书 `user_info` / Notion `users/me`）                                                                           | 用户 JWT + owner                      |
| `DELETE /v1/connections/{id}`              | 本地撤销 + 尽力调 provider revoke；仅 active 可撤，重复 409                                                                  | 用户 JWT + owner                      |
| `POST /v1/connectors/{provider}/execute`   | `{tool, arguments, run_id}`；成功 `{"ok":true,...}`，工具级失败 HTTP 200 + 错误信封                                          | **X-Internal-Secret** + `X-Tenant-Id` |
| `GET /v1/internal/connectors/tools`        | 按租户返回可用连接工具（ToolDescriptor 形状），供 tool-service 装配                                                          | **X-Internal-Secret** + `X-Tenant-Id` |

实现说明（与初稿的差异）：

- 执行端点按 **provider** 寻址而非连接 id（tool-service 只看到工具名，不知道 connection_id；v1 按「租户 + provider 取最新 active+enabled 连接」执行），connection_id 写入响应便于审计；
- 内部鉴权用共享密钥头 `X-Internal-Secret`（`common.security.internal_secret`，留空即整体 401），不引入服务级 token；网关不转发 `/internal`；
- 目录响应是 `{providers: [...]}`（字段 `name`），授权响应不含 `session_id`；连接 DTO 不返回 `owner_subject` / 工具清单（前端从 provider 目录推导）。
- 内部目录返回 `{tools: [ToolDescriptor payload]}`，每个 payload 带 `executor="connector"` 与
  `connector={"provider": "<name>"}`（tool-service 路由执行请求的唯一依据），工具名 `<PascalCase>__<tool>`、`risk="read"`；
- tool-service 侧配置 `TOOL__CONNECTOR_BASE_URL` / `TOOL__CONNECTOR_INTERNAL_SECRET`（空=关闭）/
  `TOOL__CONNECTOR_CATALOG_TTL_SECONDS=30`；目录按租户缓存 30s，撤销/新建连接最长延迟一个 TTL 生效，
  执行时仍按租户实时校验连接（不存在则 404 `connector.connection_missing`）；
- 审计落 `tool_calls.connection_id`（C2.4 已完成）：执行端点成功/失败信封顶层都带 `connection_id`（仅连接已解析时），tool-service 提取后**从模型可见 `result` 中剥离**，再写入 `tool_calls.connection_id`（内置工具为 null）；`tool_name`/`run_id` 照旧。
- 工具级失败的错误码经 orchestrator 工具事件透传到前端工具卡（C2.9 最小版）：`connection_missing` /
  `auth_expired` / `scope_missing` 三类渲染连接器恢复卡，其余错误码仍只展示错误文本。

### 5.4 授权会话时序

1. 前端调 `authorize`；connector-service 生成 `state_nonce`(随机 32B) + `code_verifier`(43–128)，落 `oauth_sessions`，TTL 10 分钟。
2. 返回 `authorize_url`（飞书 `accounts.feishu.cn/open-apis/authen/v1/authorize`；scope 空格分隔且必须含 `offline_access`；`code_challenge_method=S256`）。
3. 用户在飞书同意 → 回调带 `code`+`state`；服务端按 nonce 取会话、校验未过期且未消费，**同一事务**标记消费。
4. 用 `code` + `code_verifier` 换 token（飞书优先 **v3** `accounts.feishu.cn/oauth/v3/token`，避开 v2 的 PKCE 兼容缺陷）。
5. 拉 `user_info` 拿 `open_id`/`union_id`/name；凭证 `encrypt_credentials` 落库；写 `connections`；置会话 `completed`。
6. 302 回 `/connectors?provider=<p>&status=connected&connection_id=<id>`；失败回 `...&status=error&code=<code>`，错误码只暴露白名单（不泄漏上游原文）。

### 5.5 刷新与失效

- **触发**：懒刷新（`expires_at - 5min` 内首次使用时）+ 手动 `/refresh` + 每 24h 巡检任务（`last_refreshed_at` 超 24h）。
- **单飞**：`SELECT … FOR UPDATE` 锁连接行 → 复盘 `expires_at` 是否已被他人刷新 → 调 provider → 同事务写回 `access/refresh/expires_at`。
- **失败**：`invalid_grant`/`20037` 等置 `status=expired`、写 `last_error_code`、发 outbox `connector.connection.expired.v1`；前端卡片变「需要重新连接」。
- **飞书特例**：refresh 一次性，禁止并发；365 天强制重授权，`expires_at` 之外另存 `metadata.refresh_deadline` 做提前提示。
- **Notion 特例**：无 `expires_in`，按 401 触发一次刷新，失败置 expired；巡检用 `POST /v1/oauth/introspect`。
- **outbox**：已接入 `events.outbox`（迁移 `c4f7b1e93a52`）；`connector.connection.created/expired/revoked.v1` 与状态变更同事务写入，`CONNECTOR__OUTBOX_ENABLED=true` 时由 `ConnectorRuntime` 起后台 `OutboxDispatcher` 至少一次投递（消费方按事件 id 去重），关闭时降级为结构化日志 `connection_event`、不写库；只发生命周期事件，不发业务数据。
- **当前实现边界**：惰性刷新 + 手动刷新 + 24h 巡检均已上线。巡检（`connector/services/patrol.py`）跨租户扫 `active + enabled` 且 `last_refreshed_at` 为空或早于 `now - CONNECTOR__REFRESH_STALE_SECONDS` 的连接，逐条独立事务走同一单飞刷新路径，单条失败只留痕不中断整轮；由 `CONNECTOR__REFRESH_PATROL_ENABLED`（生产默认 true、测试关）控制。`tenant_apps` 已参与凭证解析（见 §5.2）。

### 5.6 能力暴露

provider 模块声明 manifest（dataclass，代码即真源）：

```python
ConnectorManifest(
    provider="feishu",
    name="飞书",
    auth_kind="oauth_user",
    scopes=["offline_access", "docx:document:readonly", "drive:drive:readonly", "wiki:wiki:readonly"],
    tools=[
        ConnectorTool(key="read_document", risk=ToolRisk.READ, timeout_seconds=20,
                      description="读取飞书文档纯文本", input_schema={...}),
        ...
    ],
)
```

编译规则：`ToolDescriptor(name="Feishu__read_document", executor=ExecutorKind.CONNECTOR, kind=FUNCTION, risk=READ, scopes=[...], connector={"provider": "feishu"})`。
实现上 manifest 就是 `BaseProvider` 的子类属性（`name/prefix/display_name/tools: list[ToolSpec]`），不需要单独的 connector manifest 文件。

- **tool-service**：catalog 合并 `GET /v1/internal/connectors/tools` 的结果（按租户），执行时走 `ExecutorKind.CONNECTOR` 转发到 `/execute`，审计落 `tool_calls`（D12）。
- **agent-orchestrator**：无需感知连接器，只看到普通工具；审批复用 `approval_policies`；调用事件继续写 `run_events`。
- **命名**：`Feishu__read_document` / `Notion__search`；provider 段用 PascalCase，工具段 snake_case；不允许出现 `__` 在工具名内。
- **v1 装配**：只把 `status=active && enabled=true` 的连接的只读工具暴露给 chat/task；写工具 P3 起默认 `confirm`。

### 5.7 错误码分层

| 错误码                              | HTTP（手动刷新/接口） | retryable | 前端行为                              |
| ----------------------------------- | --------------------- | --------- | ------------------------------------- |
| `connector.auth_expired`            | 409                   | 否        | 「连接需要重新连接」                  |
| `connector.oauth_failed`            | 409                   | 否        | 重新发起授权                          |
| `connector.scope_missing`           | 409                   | 否        | 展示缺失 scope，引导重新授权          |
| `connector.upstream_rate_limited`   | 429                   | 是        | 提示稍后重试，hint 带秒数             |
| `connector.upstream_timeout`        | 504                   | 是        | 可重试提示，不提示重连                |
| `connector.upstream_unavailable`    | 502                   | 是        | 提示上游暂不可用                      |
| `connector.upstream_error`          | 502                   | 视情况    | 原样提示上游错误（不回显 body）       |
| `connector.invalid_arguments`       | 400                   | 否        | 修正参数                              |
| `connector.invalid_state`           | 409                   | 否        | 状态冲突（如重复撤销）                |
| `connector.connection_not_found`    | 404                   | 否        | 列表刷新                              |
| `connector.connection_missing`      | 404                   | 否        | 引导去连接                            |
| `connector.tool_not_found`          | 400                   | 否        | 工具清单已过期，刷新目录              |
| `connector.provider_not_found`      | 400                   | 否        | 目录刷新                              |
| `connector.provider_not_configured` | 400                   | 否        | 提示管理员配置应用凭证                |
| `connector.internal_forbidden`      | 401                   | 否        | 内部调用未带/带错 `X-Internal-Secret` |

- 只有 `auth_expired` / `oauth_failed` / `scope_missing` 触发重连 UI，并把连接置
  `expired`（D7）；限流/超时/5xx 是瞬时故障，连接保持 `active`，只写 `last_error_code`。
- **执行面**（tool-service 调用）不抛 HTTP 错误：工具级失败一律 HTTP 200 +
  `{"ok":false,"error":{...}}`；HTTP 4xx 只用于「无连接 / 未知工具 / 内部鉴权」这类协议错误。
- 回调失败不返回 JSON：302 回 `/connectors?...&status=error&code=<白名单错误码>`。
- 上游原始错误只进日志与 `last_error_code`，不回显 body。

### 5.8 安全与租户边界

- 凭证列读写都不出服务；列表/详情 DTO 明确排除 `credentials`；日志脱敏（token 前缀也不打）。
- 租户隔离继续走 `TenantRepository`；跨租户一律 404。
- 回调只能由 `state` nonce 驱动；nonce 单次消费 + 10 分钟过期 + 绑定 tenant/user/provider/redirect。
- 飞书 `app_secret`/`encrypt_key` 只存服务端；`tenant_apps` 复用同一加密函数。
- 生产密钥换 KMS/长随机（现状默认值仅供 dev）；P1 补 `CONNECTOR__CREDENTIAL_SECRET` 启动强校验（生产非默认值）。
- 飞书 IP 白名单：Serverless 出口 IP 不固定时不可用，v1 不启用；改用应用级限流 + 错误告警。

## 6. 飞书连接器 v1 规格

**应用形态**：企业自建应用；全局 `FEISHU_APP_ID/FEISHU_APP_SECRET`，可按租户覆盖。开发者后台「安全设置」配置 `redirect_uri` = `https://<host>/api/connector/v1/oauth/feishu/callback`（精确匹配，可配多个）。

**身份**：`user_access_token`（谁授权以谁身份读），`offline_access` 必须拼进 scope。

**P0 只读工具**：

| 工具                           | 上游端点                                  | scope                    | 说明                                |
| ------------------------------ | ----------------------------------------- | ------------------------ | ----------------------------------- |
| `Feishu__get_me`               | `GET /authen/v1/user_info`                | 无需                     | 连接归属与连通性探测                |
| `Feishu__list_files`           | `GET /drive/v1/files`                     | `drive:drive:readonly`   | `page_token` 分页，用于「选文件」UI |
| `Feishu__read_document`        | `GET /docx/v1/documents/{id}/raw_content` | `docx:document:readonly` | 纯文本，最常用                      |
| `Feishu__read_document_blocks` | `GET /docx/v1/documents/{id}/blocks`      | `docx:document:readonly` | 递归子块，5 次/秒/应用，需截断      |
| `Feishu__list_wiki_spaces`     | `GET /wiki/v2/spaces`                     | `wiki:wiki:readonly`     | 知识空间列表                        |
| `Feishu__list_wiki_nodes`      | `GET /wiki/v2/spaces/{id}/nodes`          | `wiki:wiki:readonly`     | 节点可能指向 docx/sheet/bitable     |

**实现要点**：分页统一 `page_token`/`has_more`，单次返回限 50 条、工具结果字节上限（沿用 tool-service 截断约定）；429 读 `x-ogw-ratelimit-reset` 退避，最多重试 2 次，否则返回 `connector.upstream_rate_limited`；docx blocks 返回 400/99991400 也按限流处理（旧接口行为）；`tenant_access_token` 按 `app_id` 缓存（提前 5 分钟、单飞），v1 仅在 `auth_kind=tenant` 时使用。

**配置**：`CONNECTOR__FEISHU_APP_ID`、`CONNECTOR__FEISHU_APP_SECRET`、`CONNECTOR__FEISHU_SCOPES`（白名单，逗号分隔）、`CONNECTOR__FEISHU_REDIRECT_URI`、`CONNECTOR__PROVIDERS_ENABLED=feishu`。

## 7. Notion 预留（P4）

差异点已收敛为 provider 内实现，平台不改：

- 唯一键含 `external_workspace` + `owner_subject`；`account = f"{workspace_id}:{owner_subject}"`。
- `Notion-Version` 配置化（当前示例 `2026-03-11`）；capabilities 变更后用户需重新授权，详情页展示 capabilities。
- 无 `expires_in`：`expires_at` 留空，401 触发刷新；`introspect` 做巡检。
- 工具：`Notion__search`、`Notion__read_page`、`Notion__read_blocks`、`Notion__query_data_source`、`Notion__create_page`（WRITE/confirm）、`Notion__append_blocks`（WRITE/confirm）。
- 写工具需先读后写、幂等键用 page id；不解析 `url`/`href`/`cursor`（官方声明非稳定标识）。

## 8. 实施清单

估算单位：S ≤ 1 人日，M 2–3 人日，L 4–5 人日。**P0–P2 合计约 25–35 人日**（单人；平台与 provider/前端可并行压缩）。

> **实现状态（2026-11-04 回填）**：P1 平台骨架（C1.1–C1.12）与 P2 飞书只读 + 执行面（C2.1–C2.8）已完成；
> C0 决策项按本文默认值执行。本轮补齐 C1.10（`events` outbox 生命周期事件 + 24h 巡检 + `tenant_apps` 凭证解析）
> 与 C2.4 的 `connection_id` 审计列。
> 与清单的差异：manifest 改为 `BaseProvider` 子类而非 dataclass 注册（§5.6）；执行代理按 provider 寻址
> （`POST /v1/connectors/{provider}/execute`，§5.3）；租户应用凭证解析落在 `connector/services/apps.py`、
> 巡检在 `connector/services/patrol.py`、后台任务统一由 `ConnectorRuntime` 启停。
> **2026-11-04 规划变更**：按新规划移除内置飞书 provider（`providers/feishu.py`、飞书配置项与飞书专属测试）；平台骨架/页面/执行面/审计保留；后续 provider 待新规划。
> **未做/待办（2026-11-04 复核）**：C2.9 只落地最小版——会话内工具失败恢复卡
> （`apps/web/src/features/chat/connector-recovery-card.tsx`，三类授权错误码 → 「去连接器」，
> 用户重连后手动重发；「+ 菜单」选择与 run 暂停续跑待 C0.4）；C2.10（真实飞书冒烟）随飞书
> provider 移除不再适用（清单文件已删除）；
> P3/P4（写能力、组织共享连接、Notion/MCP）未开始。

### 8.1 P0 决策与契约（先做，阻塞后续）

- [ ] **C0.1** 确认飞书应用形态：v1 写死**企业自建应用**（每租户一套 App ID/Secret），商店应用留字段不实现 — 验收：决策记入本文 D9，产品/交付确认自建应用交付方式（M）
- [ ] **C0.2** 确认连接归属：**用户私有 + 组织内可见**，组织共享连接 P3 — 验收：迁移含 `owner_subject`，撤销审计能回答「谁授权」（S）
- [ ] **C0.3** 确认 v1 范围：**只读 + on-demand**，不做同步/事件/webhook — 验收：§1.2 分期表评审通过（S）
- [ ] **C0.4** 确认 UI 入口与文案：侧栏「连接器」、卡片式、执行中错误卡是否 v1 就做 — 验收：与设计稿对齐（S）
- [ ] **C0.5** 确认 MCP 自定义连接器是否 P4（建议是）— 验收：D11 定稿（S）

### 8.2 P1 平台骨架（connector-service）

- [x] **C1.1** 迁移：`connections` 加 12 列 + `oauth_sessions` + `tenant_apps`；`down_revision='cd993640a7eb'` — 验收：`uv run alembic upgrade head`/`alembic check` 通过，测试库 `create_all` 与迁移一致（M）
- [x] **C1.2** provider 注册表：`ConnectorManifest`/`ConnectorTool` dataclass + 装饰器注册 + `PROVIDERS_ENABLED` 过滤 — 验收：未启用 provider 不出现在目录（S）
- [x] **C1.3** 授权会话服务：nonce 生成/单次消费/PKCE/过期清理 — 验收：重放 nonce 400、过期 409、并发消费只有一个成功（M）
- [x] **C1.4** `POST /v1/connectors/{provider}/authorize` — 验收：返回可用的飞书授权 URL，含 S256 challenge（S）
- [x] **C1.5** `GET /v1/oauth/{provider}/callback`：换 token → 拉用户信息 → 加密落库 → 302；失败白名单错误码 — 验收：MockTransport 全流程，库里可解出 token，响应不含凭证（M）
- [x] **C1.6** 连接 CRUD 扩展：`PATCH`、`/refresh`、`/check`、详情含 scopes/工具；`DELETE` 补撤销留痕 — 验收：owner 校验、跨租户 404、重复撤销 409（M）
- [x] **C1.7** 刷新单飞：行锁 + 二次检查 + 同事务写回 + 失败置 expired — 验收：并发 10 次刷新只打 1 次上游、只写 1 次库（M）
- [x] **C1.8** 执行代理 `POST /v1/connections/{id}/execute`：工具查找、解密注入、超时、错误映射、usage 日志；内部 token 鉴权 — 验收：token 不出现在响应/日志；超时映射 504；上游 429 带 `retry_after`（M）
- [x] **C1.9** 内部目录 `GET /v1/internal/connectors/tools` — 验收：按租户返回只含 active+enabled 的工具（S）
- [x] **C1.10** 引入 `events` outbox，发 `connector.connection.created/expired/revoked.v1` — 验收：outbox 行 + dispatcher 测试；未启用时降级为纯日志（M）
- [x] **C1.11** 配置与文档：`.env.example`、README「接入新连接器 5 步」更新为 manifest 流程 — 验收：`check-docs` 通过（S）
- [x] **C1.12** 测试：`--cov-fail-under=91`；`pytest-socket` 零网络（全 MockTransport） — 验收：`npx nx test connector-service` 绿（M）

### 8.3 P2 飞书 provider + 前端 + 执行面

> 2026-11-04：C2.1/C2.2 的飞书 provider 已移除（平台骨架、前端、执行面与审计保留，C2.3–C2.8 仍有效）。

- [x] **C2.1** `connector/providers/feishu.py`：OAuth（v3 端点）、`user_info`、token 解密/刷新/错误码映射 — 验收：`20037`→expired、`20027`→scope_missing、429 解析 reset 头（M）
- [x] **C2.2** P0 六个只读工具 + 分页 + 递归 blocks + 结果截断 — 验收：每工具 1 条正常 + 1 条错误用例；大文档不超字节上限（M）
- [x] **C2.3** `common.tools` 新增 `ExecutorKind.CONNECTOR` + `ToolDescriptor.connector`（provider/action 可选字段） — 验收：`packages/common` 测试与 `test_tools` 兼容；未知字段反序列化不炸（S）
- [x] **C2.4** tool-service：catalog 合并连接器工具、`CONNECTOR` 执行器转发、审计字段补 `connection_id` — 验收：端到端 execute 落 `tool_calls`，run_id 透传（M）
- [x] **C2.5** agent-orchestrator：工具装配纳入连接器工具（chat 只读 / task 按审批），失败事件写 `run_events` — 验收：一次 chat 调用飞书读文档链路通（M）
- [x] **C2.6** `apps/web` 新页面 `/connectors`：卡片网格（已连接/未连接/失效）、添加 → 跳授权 → 回流 toast — 验收：`nx typecheck/test/lint` 绿；文件 ≤400 行（M）
- [x] **C2.7** 连接详情抽屉：账号、已授予 vs 期望 scope、工具清单、最近刷新、禁用开关、解绑 — 验收：失效态显示「重新连接」且只对 auth 类错误出现（M）
- [x] **C2.8** 侧栏入口 + 空态/加载/错误态；`features/connectors/` 域 + `use-connectors-model.ts` — 验收：无 `pages/` 取数，符合 eslint 分层（S）
- [ ] **C2.9** 会话内「连接器」选择（+ 菜单）与执行中授权中断卡片（若 C0.4 确认） — 验收：错误码→卡片→重连→重试一次（L）
  - **2026-11-04 部分完成（最小版 A）**：工具失败卡内渲染连接器恢复卡
    （`connector-recovery-card.tsx`），按 `connector.connection_missing` / `auth_expired` /
    `scope_missing` 给出「去连接器」（`/connectors?provider=…`）+「回会话重发」提示，折叠态也可见；
    「+ 菜单」选择与 run 暂停→授权→续跑未做，待 C0.4 决策后进 P3。
- [ ] **C2.10** 端到端冒烟：开发环境真实飞书自建应用走通「授权→列文件→读文档→撤销」 — 验收：录屏/截图存 `docs/research/connector/` 或验证报告（M）
  - **2026-11-04**：清单文件已删除；随飞书 provider 移除，本项不再适用。

### 8.4 P3 飞书写能力（按需）

- [ ] **C3.1** `docx:document` 写 scope + 创建文档/追加块工具（WRITE + confirm） — 验收：默认不暴露给 chat；`approval_policies` 命中 confirm（M）
- [ ] **C3.2** 多维表格读记录（`base:record:retrieve`，page_size≤500、20 次/秒）与电子表格读 — 验收：限流退避正确（M）
- [ ] **C3.3** 组织共享连接（`owner_subject` 为空 + 授权人留痕）与管理员可见 — 验收：租户内非 owner 可列出但不可撤销（L）
- [ ] **C3.4** 飞书事件长连接（可选）：先落库再异步、`event_id` 幂等、50 连接上限 — 验收：断开重连不丢事件（L）

### 8.5 P4 Notion + MCP 并入

- [ ] **C4.1** `connector/providers/notion.py`：OAuth（Basic Auth 换 token）、capabilities、`Notion-Version` 配置、未知 TTL 处理 — 验收：401→refresh 一次→失败置 expired（M）
- [ ] **C4.2** Notion 六个工具 + data source 查询单独 3 次/秒限流 — 验收：workspace 共享预算不互相打爆（M）
- [ ] **C4.3** 连接详情展示 Notion workspace / owner / capabilities — 验收：多 workspace 同账号不覆盖（S）
- [ ] **C4.4** MCP 自定义连接器并入 UI（`kind=mcp`，凭证仍走 `mcp_oauth`） — 验收：审批/审计与原生 provider 一致（L）

### 8.6 质量与门禁（每期收口）

- [x] **C5.1** `npx nx lint/test connector-service tool-service agent-orchestrator common` 全绿（含覆盖率阈值）
      — 实证（2026-11-04）：connector-service 82 passed / 96.60%、tool-service 343 passed / 89.66%、
      common 155 passed、agent-orchestrator 938 passed；四个项目 ruff lint 全绿。
- [x] **C5.2** `npx nx typecheck/test/lint @beauty/web -- --run` 全绿；`bash scripts/refactor-gate.sh @beauty/web`
      — web 的 lint/typecheck/test/build/docs/workspace 六步全绿；refactor-gate 的 `py-size` 仍有 3 个超 500 行文件
      （`tool-service/tests/test_connectors.py`、`connector-service/connector/providers/feishu.py`、
      `agent-orchestrator/tests/test_skill_packages.py`），三者在 HEAD 上即已超限，属既有 W5 ratchet 基线，
      不由本期引入；留待 W5 体量收敛处理。
- [x] **C5.3** `cd services/connector-service && uv run alembic check`；`bash scripts/migrate-local.sh` 本地升级通过
      — 两服务 `alembic check` 均输出 No new upgrade operations detected；本地库已升级到各服务 head
      （connector-service `c4f7b1e93a52`、tool-service `0004_tool_call_connection`）。
- [x] **C5.4** `bash scripts/check-docs.sh` + `bash scripts/check-workspace.sh`（索引覆盖）
      — 均通过：docs prettier + 61 个文件链接检查 0 失败；归档注记/索引覆盖/无生成物/导出配置 4 项 PASS。
- [x] **C5.5** `docker compose -f deploy/docker-compose.yml config -q` 与 `scripts/golden-run/golden-run.sh` 通过
      — compose 静态校验通过；黄金 run 9/9 阶段 PASS（期间按 P1-D 事件流更新了 `FakeLLM` 与事件序列断言，
      `run.item.v1` 过程项不参与主链序列校验）。
- [x] **C5.6** 安全自查：凭证不出服务、日志脱敏、回调 nonce 单次、跨租户 404、内部端点不经网关
      — `ConnectionRead`/`ConnectionBrief` 不含 credentials/refresh_token（`test_connections` 断言列表与详情无凭证）；
      事件与日志只带 connection_id/provider/account/status/owner_subject；`test_oauth` 覆盖 state 重放（`connector.state_used`）
      与过期；`test_auth`/`test_connections::test_tenant_isolation` 覆盖跨租户 404；API 网关只注入 Authorization
      等固定头，透传不了 `X-Internal-Secret`/`X-Tenant-Id`，内部面仅 tool-service 直连携带共享密钥。

## 9. 风险与开放问题

### 9.1 待产品确认（阻塞 P1）

1. **飞书应用交付方式**：每客户自建应用意味着交付时要「建应用 + 配 redirect + 发版审批」，客户管理员参与；是否接受？若要做 SaaS 自助接入，需 ISV 商店应用（资质 + 上架审核 + 放弃长连接）→ 影响 D9。
2. **连接归属**：用户私有（推荐）还是组织共享？共享需要定义谁能撤销、离职如何处理。
3. **首版是否只读**：只读能覆盖「读文档/知识库喂模型」；写文档是否 P1 就要。
4. **执行中授权中断**：v1 是否做「run 暂停→授权→续跑」，还是只做「失败卡 + 手动重试」。
5. **数据边界**：飞书文档内容进入模型与日志的范围、留痕与脱敏要求（是否要内容级审计）。

### 9.2 风险登记

| 风险                               | 影响                   | 缓解                                                                |
| ---------------------------------- | ---------------------- | ------------------------------------------------------------------- |
| 飞书 scope 变更需发版 + 管理员审批 | 权限迭代天级，扩能力慢 | scope 白名单配置化；详情页展示「已授予 vs 期望」；提前规划 P1 scope |
| refresh_token 一次性 + 多副本      | 并发刷新导致连接失效   | 数据库行锁单飞 + 二次检查；巡检任务单实例或加锁                     |
| 365 天强制重授权                   | 长期连接必然失效       | `metadata.refresh_deadline` 提前 30/7 天提示；失效错误码明确        |
| docx blocks 递归 + 大文档          | 超时/超大响应          | 深度与字节双上限；截断标记 + 分页工具                               |
| 飞书限流（API×应用×租户）          | 批量读触发 429         | 令牌桶按三元组 + 读 reset 头退避；错误可重试                        |
| 凭证密钥泄露                       | 全部第三方 token 暴露  | KMS/长随机 + 启动强校验；撤销路径可批量执行                         |
| 连接器工具污染模型工具面           | 模型选择困难/误用      | 工具按连接启用；描述精简；写工具 confirm；每连接工具数上限          |
| Notion token TTL 未知              | 被动 401，可能影响体验 | 401 触发刷新 + introspect 巡检；`expires_at` 空表示未知             |

## 10. 参考

**仓库内**

- [01-workbuddy.md](../research/connector/01-workbuddy.md)：WorkBuddy 连接器产品形态、三档 auth、UX、与 connector-service 对照
- [02-mainstream.md](../research/connector/02-mainstream.md)：Nango/Composio/Arcade/Paragon/Merge/Airbyte/MCP 横评与五条借鉴
- [03-feishu-notion.md](../research/connector/03-feishu-notion.md)：飞书/Notion 官方文档逐页取证、最小能力清单、差异点
- [architecture.md](../architecture.md)、[tool-service.md](./tool-service.md)、[tool-execution.md](./tool-execution.md)

**外部**

- WorkBuddy 连接器：<https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector>、<https://open.workbuddy.cn/docs/connector>
- 飞书开放平台：<https://open.feishu.cn/document/home/app-types-introduction/overview.md>、<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token.md>
- Notion：<https://developers.notion.com/guides/authorization/overview.md>、<https://developers.notion.com/reference/request-limits.md>
- 授权安全：RFC 9700（OAuth 2.0 Security BCP）、RFC 7636（PKCE）
