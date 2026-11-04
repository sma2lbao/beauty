# 连接器平台（Connector Platform）设计

> 状态：已实现（P1 平台骨架；2026-11-04 二次调整后**无内置 provider**，Notion 改走官方 MCP） · 2026-11-03 定稿 / 2026-11-04 回填实现状态、移除内置 Notion provider
> 关联：[architecture.md](../architecture.md) §8.2（MCP client）、§11.2（审批）、[tool-service.md](./tool-service.md)、[01-workbuddy.md](../research/connector/01-workbuddy.md)、[02-mainstream.md](../research/connector/02-mainstream.md)、[03-feishu-notion.md](../research/connector/03-feishu-notion.md)
> 定位：本文是「连接器」这一能力域的单一真源，回答**做什么、照谁做、先做哪个、按什么顺序做**；服务边界仍以 [architecture.md](../architecture.md) 为准。
> 现状：P1 平台已落地——授权会话、连接 CRUD/撤销、刷新单飞 + 24h 巡检、outbox 生命周期事件、tool-service 执行面与 `/connectors` 页面；**内置 provider 目录为空**：内置飞书 provider（2026-11-04 上旬移除）与内置 **Notion** provider（provider key `notion`、4 只读 + 2 写工具）均已按 2026-11-04 二次规划移除，平台 API、`tenant_apps` 与静态令牌连接能力保留给后续 provider。**Notion 现通过官方 MCP 接入**（`https://mcp.notion.com/mcp`，见 §6）：`/connectors` 改为 MCP 预设连接器页（MCP 唯一入口：预设卡片 + 页内「我的 MCP 服务」区；侧栏不再单列 MCP，`/mcp` 仅兼容直链），一键授权后由 agent-orchestrator 的 MCP client 托管凭证与工具，风险按 MCP 工具 `annotations` + 预设只读前缀分档（§7）；对话内写操作确认链路（`chat_tool_confirmations`）保留，对 `risk=write` 的 MCP 工具同样生效。P3 剩余组织共享/授权展示与 P4（其他 provider）未开始。
> **2026-11-04 规划变更（二次）**：删除内置 Notion provider（`connector/providers/notion.py`、`CONNECTOR__NOTION_*` 配置项与 Notion provider 专项测试），connector-service 收敛为**通用连接器平台（当前无内置 provider）**；Notion 改为官方 MCP `https://mcp.notion.com/mcp`，`/connectors` 页面改为 **MCP 预设连接器（一键授权）+「我的 MCP 服务」区**（MCP 唯一入口，侧栏不再单列；`/mcp` 仅兼容直链）；agent-orchestrator 按 MCP 工具 `annotations` + 预设 read 前缀做风险分级（读直接执行、写在对话内出确认卡、破坏性工具不进对话，见 §7）。平台骨架/页面/执行面/审计保留。

## 1. 摘要

一句话：把 `connector-service`（:8004）升级为**连接器平台**——统一承载 OAuth 授权、凭证托管、token 刷新、工具暴露与执行代理；连接器本体用**代码 manifest** 声明。2026-11-04 二次调整后**平台保留、内置 provider 目录为空**；Notion 不再内置，改由 agent-orchestrator 的 MCP client 接官方 MCP（`https://mcp.notion.com/mcp`），`/connectors` 变为 MCP 预设连接器（一键授权）+「我的 MCP 服务」区，成为 MCP 唯一入口（`/mcp` 仅兼容直链）。

三条主线（也是本稿与「直接接 MCP」的分野）：

1. **连接语义 = 授权生命周期，不绑定实现形态**。飞书/Notion 都需要「用户以自己身份授权、平台代其读写」；有官方 MCP 的 SaaS 直接走 MCP（Notion），没有的再进 connector-service 的 provider 层——两条路径共用「凭证零出口 + 能力即工具 + 审批审计」的语义。
2. **凭证零出口**。第三方 token 只存在 connector-service 的加密列里；agent、tool-service、前端都拿不到 token，只能发「工具名 + 参数」。这条对齐 WorkBuddy 的 `server-side` 档与 Composio/Arcade 的做法。
3. **能力即工具**。每个 provider 能力编译成现有 `ToolDescriptor`，复用 `auto|confirm|deny` 审批与 `run_events` 审计；不为连接器另造一套权限与追踪。

### 1.1 关键决策（D1–D13）

| #   | 决策                                                                                                                                                                                                                                                     | 理由 / 依据                                                                                                                                                                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | **connector-service 做平台层**；每 provider 一个模块（`connector/providers/<key>.py`），能力用**代码 manifest** 声明，不引入 YAML / builder（当前目录为空：内置 Notion provider 已移除，见 §6）                                                          | 定义层与连接层分离（Composio AuthConfig vs ConnectedAccount）；Nango 已废弃 `nango.yaml`（带 YAML 的部署被直接拒绝），是最有力的反向证据                                        |
| D2  | 连接**归属到授权用户**（`owner_subject`），组织内按权限可见；组织共享连接（无 owner）留到 P2                                                                                                                                                             | 撤销与审计必须能回答「谁授权的」；现状 `connections` 无归属字段是明确缺口                                                                                                       |
| D3  | 授权走**服务端一次性授权会话**：`oauth_sessions` 存 state nonce / PKCE verifier / tenant / user / redirect / 过期，回调用 nonce 换回身份                                                                                                                 | state 只放随机数、身份靠服务端行查回；RFC 9700 强制 PKCE(S256)、精确 redirect、禁隐式流                                                                                         |
| D4  | **凭证零出口**：agent 侧新增 `/v1/connections/{id}/execute`，由 connector-service 解密并注入 token；token 不下发给 tool-service 或前端                                                                                                                   | WorkBuddy `server-side`、Composio Proxy Execute、Arcade token vault 同构；解密点收敛到一处，泄漏面可控                                                                          |
| D5  | **刷新单飞**：按连接行加数据库行锁 + 二次检查 + 同事务写回；到期前 5 分钟懒刷新 + 每 24h 巡检保活；失败置 `expired` 并发 outbox 事件                                                                                                                     | 飞书 `refresh_token` 一次性、旧的立即失效，多副本用进程锁会互相覆盖；Nango 24h 保活可防 provider 回收闲置 token                                                                 |
| D6  | 能力编译为 `ToolDescriptor`，命名 **`<Provider>__<tool>`**（provider 段 PascalCase、工具段 snake_case）；MCP 工具用 `mcp__<server>__<tool>` 区分；执行面走 tool-service                                                                                  | 复用 catalog/审批/审计，不新建权限体系；`mcp__` 是外部协议命名，连接器是自有能力，用可读前缀更利于模型选择                                                                      |
| D7  | **错误语义分层**：`connector.auth_expired` 与 `connector.upstream_*` 分开；只有 auth/scope 类错误触发前端「重新连接」                                                                                                                                    | WorkBuddy 明确只有授权失效才提示重连，超时/无权限不提示；否则所有 4xx/5xx 都会误导用户                                                                                          |
| D8  | **只做 on-demand 拉取**：v1 不做同步引擎、不做事件订阅、不做后台定时抓取                                                                                                                                                                                 | WorkBuddy 官方「不主动定时抓取」；省掉同步状态机/游标/去重一整块；与架构现状一致                                                                                                |
| D9  | **飞书 v1 写死企业自建应用 + 用户身份（`user_access_token`）+ 只读能力**；应用凭证全局配置 + `tenant_apps` 表按租户覆盖                                                                                                                                  | 长连接仅自建应用支持、商店应用需 ISV 资质与上架审核；用户身份才符合「谁授权以谁身份读文档」；写 scope 要重新发版 + 管理员审批，边际收益低                                       |
| D10 | **Notion 曾放 P2（2026-11-04 撤销）**：原计划内置 provider（public integration + 4 只读 + 2 写），现改为官方 MCP（§6），内置 provider 已删除                                                                                                             | Notion 一个 token 绑「一个 workspace + 一个用户」；官方 MCP 已具备 DCR/PKCE/refresh 全链路，凭证与工具由 orchestrator 的 `mcp_oauth` / `mcp_server_auth` 托管，租户隔离沿用该表 |
| D11 | **MCP 从「P2 扩展来源」升格为 Notion 的主路径**：内置预设 + 一键授权（§6/§7）；把任意 MCP server 并入「连接器」UI 与原生 provider 同列仍留 P4，两者不混凭证模型（自有加密列 vs `mcp_server_auth`）                                                       | 2026-11-04 实测官方 MCP 具备完整 OAuth 链路（DCR/PKCE/refresh），平台无需为 Notion 再写 provider 代码与分页/限流适配                                                            |
| D12 | 所有连接器调用**必须经 tool-service**（审计唯一落 `tool_calls`）；connector-service 不建第二张调用审计表                                                                                                                                                 | 对齐 tool-service 设计 D8「审计单源」；connector-service 只记结构化日志与 usage 指标                                                                                            |
| D13 | 写类工具（创建/更新/删除类，含 MCP 写工具）**默认 risk=WRITE，执行前必须人工确认**：任务路径走审批闸（confirm），对话路径走就地确认（pending 落库 + confirm/cancel 端点）；DANGER 工具（`annotations.destructiveHint` 或未信任的非只读工具）不在对话暴露 | 写错文档不可逆、用户信任成本高；对话没有任务图审批流，用「先生成待确认、用户点击后以 approved=True 重放」补齐，同时不放宽 tool-service 的写闸门                                 |

### 1.2 分期

| 期  | 内容                                                                                | 可回滚性                        | 状态                          |
| --- | ----------------------------------------------------------------------------------- | ------------------------------- | ----------------------------- |
| P0  | 决策确认（§9.1）+ 迁表设计                                                          | —                               | 已定稿                        |
| P1  | 平台骨架：授权会话 / 连接创建 / 刷新 / 撤销 / 执行代理 / provider 注册表            | `CONNECTOR__PROVIDERS_ENABLED=` | 已实现（当前无内置 provider） |
| P2  | 内置 Notion provider（4 只读 + 2 写工具）+ 前端 `/connectors` + tool-service 执行面 | 同上（按 provider 关）          | 已实现后撤销（2026-11-04）    |
| P3  | 组织共享连接 + 连接详情授权展示（内置写工具部分随 provider 移除，见 §8.4）          | 按工具关闭                      | 部分完成                      |
| P4  | 其他 provider（代码 manifest）+ 任意 MCP server 并入连接器 UI                       | 按 provider 关                  | 未开始                        |
| P5  | MCP 预设连接器（Notion 一键授权）+ annotations 风险分级（2026-11-04 二次变更）      | 按 server 停用                  | 已实现                        |

## 2. 实现前现状盘点（2026-11-03，保留作背景）

> 本节是初稿时的缺口清单；P1/P2 已按 §8 清单补齐，落地情况见 §8 实现状态与 §5.3/§5.5 的实现边界。
> 2026-11-04 二次变更：内置 provider 从飞书改为 Notion 后，又整体移除（Notion 改走官方 MCP）；本节其余内容仍作背景。

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

> 下表是 2026-11-03 做内置 provider 选型时的调研对比；2026-11-04 二次变更后 Notion 走官方 MCP，本表仅作历史依据。

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

| 方案                    | 做法                                                                                          | 优点                                             | 缺点                                                                                                                                                                               |
| ----------------------- | --------------------------------------------------------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A. MCP-first            | 把 SaaS 的官方 MCP server 装进现有 orchestrator MCP client，零新代码                          | 最快出 demo；复用 DCR/PKCE/stdio                 | 2026-11-03 判断「飞书/Notion 无官方 MCP」——2026-11-04 复核 **Notion 已有官方 MCP**（见下方结论调整）；凭证在 MCP 进程里，租户隔离/审批/审计归不到连接对象；消费者授权没有 DCR 场景 |
| B. 纯原生适配器         | 在 connector-service 内为每个 provider 写 OAuth + 工具 + 执行                                 | 完全可控；凭证零出口；审计统一                   | 每个 provider 重复写 OAuth/刷新/分页/限流                                                                                                                                          |
| C. 平台 + 原生 provider | connector-service 做连接平台；飞书/Notion 用代码 manifest 声明 provider；MCP 作为 P2 扩展来源 | 新 provider 只写 provider 模块；平台能力只做一次 | 首版要建平台骨架（授权会话/刷新/执行代理），约多 5 人日                                                                                                                            |

**推荐 C**。理由：飞书/Notion 的差异集中在 provider 层（token 形态、分页、限流、权限模型），平台层（连接生命周期、凭证加密、执行代理、错误分层）完全可以共用；若走 A，将来做「组织共享连接 / 撤销审计 / 按工具审批」时仍要补回 B 的全部工作，等于做两遍。

**边界**：C 不是「什么都自建」——不抄 Unified API 归一化、不做同步引擎、不外包 SaaS 凭证托管、不引入 MCP 的 DCR/PRM 全套给内部 agent。

> **2026-11-04 二次变更（结论调整）**：Notion 已有官方 MCP（DCR/PKCE/refresh 全链路可用），因此 Notion 改走 **A（MCP-first，零新 provider 代码）**；connector-service 的平台能力（P1）保留，供没有官方 MCP 的 SaaS 按 C 接入。原 A 行缺点中的「无官方 MCP」对 Notion 已不成立。

## 5. 目标架构

### 5.1 组件与数据流

```text
【MCP 预设连接器（Notion 现行路径，§6）】
浏览器 /connectors
   │  POST /api/v1/mcp/presets/notion          （幂等 ensure：本租户已有同 url server 则直接返回）
   │  POST /api/v1/mcp/servers/{id}/oauth/start {"next":"/connectors"}
   ▼
agent-orchestrator ── mcp_oauth：RFC 9728 PRM → AS 元数据 → DCR(RFC 7591) → PKCE S256
   │◀────── { authorize_url } ────────────────┘
   │ 浏览器跳 AS 授权页 ── 用户同意 ──▶ 授权服务器回调
   │        GET /api/mcp/oauth/callback?code&state（API 网关同源转发，带 X-Internal-Secret）
   │                                   │ state 校验 → code+PKCE 换 token → 加密落 mcp_server_auth
   │                                   │ 尽力刷新一次 tools_cache → 302
   ▼                                   ▼
/connectors?oauth=success&server=Notion   （next 白名单以 /connectors 为主，/mcp 仅兼容直链）
   │
agent-orchestrator ── MCP tools/list（工具面以 server 为准，当前约 70 个）
                     ── 工具风险：annotations + 预设 read 前缀（§7）
                     ── 会话里选中该 server（?mcp=<id>）后由模型调用 https://mcp.notion.com/mcp
```

```text
【连接器平台（connector-service，当前无内置 provider；保留给后续 provider）】
浏览器 /connectors ── POST /api/connector/v1/connectors/{provider}/authorize
   ▼
api-gateway ──▶ connector-service ── 建 oauth_sessions（state nonce；provider 声明支持时可带 PKCE）
   │◀────── { authorize_url } ────────────────┘
   │ 302 provider 授权页 ── 用户同意 ──▶ provider 回调 /api/connector/v1/oauth/{provider}/callback
   │                                   │ 校验 nonce 单次消费 → code 换 token → 加密落 connections → 302 回
   │                                   │ /connectors?provider={p}&status=connected&connection_id=…
   ▼
agent-orchestrator ── tool-service ── POST /v1/connectors/{provider}/execute {tool, args, run_id}
                                          │ 解密注入 token + 限流 + 错误映射
                                          ▼
                                     provider API
```

要点：浏览器只经手 `authorize_url` 与回调重定向；agent 只经手工具名与参数；token 只在加密列里出现（MCP 路径在 `mcp_server_auth`，连接器路径在 `connections.credentials`）。两条路径共用同一套「能力即工具 + 审批 + `tool_calls` 审计」语义。

### 5.2 数据模型

**connections 扩展**（迁移 `8f1c2a5d7b90`，`down_revision = 'b2e4c7a91d33'`）：

| 列                          | 类型                  | 说明                                                                         |
| --------------------------- | --------------------- | ---------------------------------------------------------------------------- |
| `owner_subject`             | String(64)            | 授权用户的 subject（JWT `sub`）；组织共享连接为空，P3 再补                   |
| `auth_kind`                 | String(16)            | `user`（OAuth 授权）/ `token`（用户粘贴静态令牌）；飞书 v1 为 `user`（历史） |
| `provider_account_id`       | String(128)           | 飞书 `open_id` / Notion `bot_id`                                             |
| `external_workspace`        | String(128)           | 飞书 `tenant_key` / Notion `workspace_id`                                    |
| `scopes`                    | JSON                  | 实际授予的 scope 列表                                                        |
| `metadata`                  | JSON                  | provider 附加信息（workspace_name、user_name、app_id 等）                    |
| `enabled`                   | Boolean               | 「关闭启用」≠「解绑」；默认 true                                             |
| `expires_at`                | DateTime(tz)          | access token 过期时间；Notion 未知时为空                                     |
| `last_refreshed_at`         | DateTime(tz)          | 最近一次成功刷新                                                             |
| `last_error_code`           | String(64)            | 最近一次失败码，用于前端展示                                                 |
| `revoked_at` / `revoked_by` | DateTime / String(64) | 撤销留痕                                                                     |

保留现有唯一约束 `(tenant_id, provider, account)`；`account` 写稳定可读标识（飞书 `open_id`，Notion OAuth 为 `workspace_id:owner_subject`、静态令牌为 `integration:<bot_id>`），并在 provider 内校验，避免同账号重复授权。

**oauth_sessions**（新表）：`id`(32 hex)、`tenant_id`、`owner_subject`、`provider`、`state_nonce`(unique, 64)、`code_verifier`(nullable)、`redirect_uri`、`scopes`、`status`(`pending|completed|failed|expired`)、`error_code`、`connection_id`、`expires_at`、`created_at`、`completed_at`。**回调成功/token 落库后再置 completed**；重复 nonce 一律 400。

**tenant_apps**（新表，可选覆盖）：`tenant_id`、`provider`、`app_id`、`app_secret_encrypted`(JSON)、`config`(JSON)，唯一 `(tenant_id, provider)`。解析顺序：租户行 → 全局 Settings，实现在 `connector/services/apps.py`（`app_id` 与 `app_secret` 都可用才生效，半配置行记 warning 后回退全局）；授权、刷新、巡检与执行面均按租户解析 provider，目录的 `configured` 仍用全局配置、不暴露租户凭证。v1 不做管理 UI（先配置/脚本写入）。2026-11-04 二次变更后无内置 provider：`tenant_apps` 与静态令牌连接能力保留给后续 provider；Notion 的 OAuth 客户端与 token 由 agent-orchestrator 的 `mcp_server_auth` 托管，不再使用本表。

**outbox_events / processed_events**（迁移 `c4f7b1e93a52`，`down_revision = '8f1c2a5d7b90'`）：与 `events.outbox` 同源，存连接生命周期事件（`created/expired/revoked`）供至少一次投递；`processed_events` 供消费方按事件 id 幂等。`CONNECTOR__OUTBOX_ENABLED=false` 时不写表、只落结构化日志。

### 5.3 API 契约（connector-service）

> 2026-11-04 二次变更：平台 API 保留，**当前无内置 provider**（目录为空）。直接后果：`GET /v1/connectors` 返回空列表，`POST /v1/connectors/{provider}/authorize` / `execute` 对任何 provider 都回 404 `connector.provider_not_found`；静态令牌连接（`POST /v1/connections`）仍可用，但同样需要已注册的 provider。Notion 的授权与工具已改走 MCP（§6），不再经过本节端点。

| 方法/路径                                  | 说明                                                                                                                                                                                                                                         | 鉴权                                  |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| `GET /v1/connectors`                       | provider 目录：名称、`auth_kind`、所需 scope、工具清单（含 risk）、是否已配置、`token_connectable`（是否支持粘贴静态令牌）                                                                                                                   | 用户 JWT                              |
| `POST /v1/connectors/{provider}/authorize` | 建授权会话，返回 `{provider, authorize_url, expires_at}`（session 只在服务端）                                                                                                                                                               | 用户 JWT                              |
| `GET /v1/oauth/{provider}/callback`        | 校验 state → 换 token → 落库 → 302 回 `/connectors?provider=&status=connected&connection_id=`（失败带 `status=error&code=`）                                                                                                                 | 无（一次性 nonce）                    |
| `GET /v1/connections`                      | 列表，可按 `provider`/`status`/`enabled` 过滤；永不返回 credentials                                                                                                                                                                          | 用户 JWT                              |
| `POST /v1/connections`                     | 静态令牌连接：`{provider, token, display_name?}` → 201 `ConnectionRead`；`auth_kind="token"`，不建授权会话、无回调/PKCE；不支持令牌连接 400 `connector.token_not_supported`、令牌无效 400 `connector.invalid_credentials`、未知 provider 404 | 用户 JWT                              |
| `GET /v1/connections/{id}`                 | 详情：scopes 已授予/期望、工具、`expires_at`、`last_error_code`                                                                                                                                                                              | 用户 JWT                              |
| `PATCH /v1/connections/{id}`               | `{display_name?, enabled?}`                                                                                                                                                                                                                  | 用户 JWT + owner                      |
| `POST /v1/connections/{id}/refresh`        | 手动刷新（单飞）；成功返回连接；失败按错误码映射 409/429/504/502（§5.7）                                                                                                                                                                     | 用户 JWT + owner                      |
| `POST /v1/connections/{id}/check`          | 连通性探测（飞书 `user_info` / Notion `users/me`）                                                                                                                                                                                           | 用户 JWT + owner                      |
| `DELETE /v1/connections/{id}`              | 本地撤销 + 尽力调 provider revoke；仅 active 可撤，重复 409                                                                                                                                                                                  | 用户 JWT + owner                      |
| `POST /v1/connectors/{provider}/execute`   | `{tool, arguments, run_id}`；成功 `{"ok":true,...}`，工具级失败 HTTP 200 + 错误信封                                                                                                                                                          | **X-Internal-Secret** + `X-Tenant-Id` |
| `GET /v1/internal/connectors/tools`        | 按租户返回可用连接工具（ToolDescriptor 形状），供 tool-service 装配                                                                                                                                                                          | **X-Internal-Secret** + `X-Tenant-Id` |

实现说明（与初稿的差异）：

- 执行端点按 **provider** 寻址而非连接 id（tool-service 只看到工具名，不知道 connection_id；v1 按「租户 + provider 取最新 active+enabled 连接」执行），connection_id 写入响应便于审计；
- 内部鉴权用共享密钥头 `X-Internal-Secret`（`common.security.internal_secret`，留空即整体 401），不引入服务级 token；网关不转发 `/internal`；
- 目录响应是 `{providers: [...]}`（字段 `name`），授权响应不含 `session_id`；连接 DTO 不返回 `owner_subject` / 工具清单（前端从 provider 目录推导）。
- 内部目录返回 `{tools: [ToolDescriptor payload]}`，每个 payload 带 `executor="connector"` 与
  `connector={"provider": "<name>"}`（tool-service 路由执行请求的唯一依据），工具名 `<PascalCase>__<tool>`、`risk` 取自 `ToolSpec.risk`（`read`/`write`，写工具需确认）；
- tool-service 侧配置 `TOOL__CONNECTOR_BASE_URL` / `TOOL__CONNECTOR_INTERNAL_SECRET`（空=关闭）/
  `TOOL__CONNECTOR_CATALOG_TTL_SECONDS=30`；目录按租户缓存 30s，撤销/新建连接最长延迟一个 TTL 生效，
  执行时仍按租户实时校验连接（不存在则 404 `connector.connection_missing`）；
- 审计落 `tool_calls.connection_id`（C2.4 已完成）：执行端点成功/失败信封顶层都带 `connection_id`（仅连接已解析时），tool-service 提取后**从模型可见 `result` 中剥离**，再写入 `tool_calls.connection_id`（内置工具为 null）；`tool_name`/`run_id` 照旧。
- 工具级失败的错误码经 orchestrator 工具事件透传到前端工具卡（C2.9 最小版）：`connection_missing` /
  `auth_expired` / `scope_missing` 三类渲染连接器恢复卡，其余错误码仍只展示错误文本。

### 5.4 授权会话时序（平台机制）

> 2026-11-04 二次变更后平台无内置 provider，本节只保留通用机制；具体 provider 的端点、参数与身份差异由 manifest 实现。Notion 的授权已改走 MCP（§6），不再经过本节。

1. 前端调 `POST /v1/connectors/{provider}/authorize`；connector-service 生成 `state_nonce`（随机 32B）落 `oauth_sessions`，TTL 10 分钟；provider 声明支持 PKCE 时同时生成 `code_verifier`。
2. 返回 `authorize_url`（provider 授权端点 + `client_id` / `redirect_uri` / `response_type=code` / `state` / scope，scope 按 manifest）。
3. 用户授权后回调带 `code` + `state`；服务端按 nonce 取会话、校验未过期且未消费，**同一事务**标记消费。
4. 用 `code` 换 token（provider manifest 的 `exchange_code`；client secret 只在请求头/请求体出现，不落日志、不回显）。
5. 拉身份（`fetch_identity`）取 provider 账号/工作区标识；`account` 写稳定可读标识；凭证 `encrypt_credentials` 落库；写 `connections`；置会话 `completed`。
6. 302 回 `/connectors?provider=<p>&status=connected&connection_id=<id>`；失败回 `...&status=error&code=<code>`，错误码只暴露白名单（不泄漏上游原文）。

**令牌连接（`auth_kind=token`）不经过本节**：不建 `oauth_sessions`、无 `state`/PKCE/回调，直接 `POST /v1/connections` 校验令牌身份后落库（§5.3）；平台语义保留，当前无 provider 可提供该路径（Notion 的静态令牌连接已随内置 provider 移除）。

### 5.5 刷新与失效

- **触发**：懒刷新（`expires_at - 5min` 内首次使用时）+ 手动 `/refresh` + 每 24h 巡检任务（`last_refreshed_at` 超 24h）。
- **单飞**：`SELECT … FOR UPDATE` 锁连接行 → 复盘 `expires_at` 是否已被他人刷新 → 调 provider → 同事务写回 `access/refresh/expires_at`。
- **失败**：`invalid_grant`/`20037` 等置 `status=expired`、写 `last_error_code`、发 outbox `connector.connection.expired.v1`；前端卡片变「需要重新连接」。
- **provider 特例（历史，随内置 Notion provider 移除）**：原内置 Notion provider 的 token 响应无 `expires_in`，`expires_at` 留空表示未知；401 触发一次 refresh、24h 巡检按 `last_refreshed_at` 判定。平台侧的懒刷新/单飞/巡检机制不变，供后续 provider 复用。
- **令牌连接特例**：`auth_kind=token` 的连接无 `expires_at`、无 refresh token——手动 / 懒刷新会因缺 refresh token 失败（`connector.auth_expired`），属预期；正常调用遇 401 置 `status=expired` + `last_error_code`，前端提示重新粘贴令牌；24h 巡检按 `last_refreshed_at` 同样会扫到令牌连接，刷新失败只留痕、不销毁连接。
- **飞书特例（历史，provider 已移除）**：refresh 一次性、禁止并发；365 天强制重授权，`expires_at` 之外另存 `metadata.refresh_deadline` 做提前提示。
- **outbox**：已接入 `events.outbox`（迁移 `c4f7b1e93a52`）；`connector.connection.created/expired/revoked.v1` 与状态变更同事务写入，`CONNECTOR__OUTBOX_ENABLED=true` 时由 `ConnectorRuntime` 起后台 `OutboxDispatcher` 至少一次投递（消费方按事件 id 去重），关闭时降级为结构化日志 `connection_event`、不写库；只发生命周期事件，不发业务数据。
- **当前实现边界**：惰性刷新 + 手动刷新 + 24h 巡检均已上线。巡检（`connector/services/patrol.py`）跨租户扫 `active + enabled` 且 `last_refreshed_at` 为空或早于 `now - CONNECTOR__REFRESH_STALE_SECONDS` 的连接，逐条独立事务走同一单飞刷新路径，单条失败只留痕不中断整轮；由 `CONNECTOR__REFRESH_PATROL_ENABLED`（生产默认 true、测试关）控制。`tenant_apps` 已参与凭证解析（见 §5.2）。

### 5.6 能力暴露

connector-service 的 provider 用代码 manifest 声明能力（`BaseProvider` 子类，代码即真源）：

```python
class BaseProvider:
    name: str          # 目录 key，如 "notion"
    prefix: str        # 工具名前缀，如 "Notion"
    display_name: str
    auth_kind: str     # "user"（OAuth）/ "token"（静态令牌）
    tools: tuple[ToolSpec, ...]  # ToolSpec(name, title, description, input_schema, risk)
```

**2026-11-04 二次变更后 `connector/providers/` 目录为空**：内置 Notion provider（4 只读 + 2 写）已随规划移除，`/v1/connectors` 返回空目录；平台仍保留 manifest 注册/编译/执行链路，供后续没有官方 MCP 的 provider 接入。Notion 的工具面改由官方 MCP 提供（§6/§7）。

编译规则（对任何 provider 生效）：`ToolDescriptor(name="<Provider>__<tool>", executor=ExecutorKind.CONNECTOR, kind=FUNCTION, risk=<ToolSpec.risk>, scopes=[], connector={"provider": "<name>"})`；**`risk` 由 `ToolSpec.risk` 声明并原样透传**（`read`/`write`），不在 catalog 里另立规则。

- **tool-service**：catalog 合并 `GET /v1/internal/connectors/tools` 的结果（按租户），执行时走 `ExecutorKind.CONNECTOR` 转发到 `/execute`，审计落 `tool_calls`（D12）。
- **agent-orchestrator**：不感知连接器内部，只看到普通工具；审批复用 `approval_policies`；调用事件继续写 `run_events`。
- **命名**：`<Provider>__<tool>`，provider 段 PascalCase、工具段 snake_case；MCP 工具走 `mcp__<server>__<tool>`，两者命名空间不混。
- **装配**：只把 `status=active && enabled=true` 的连接的**全部工具（含写工具）**暴露给 chat/task，`risk` 随描述符透出；chat 路径 `risk=write` 不直接执行（先落 pending 确认，§7），task 路径走 `approval_policies` 的 `confirm` 闸；`danger` 工具不进对话。
- **MCP 工具不走这条编译链**：由 orchestrator 的 MCP client 直接生成 `mcp__<server>__<tool>`，风险按 §7 的 annotations/预设规则分级。

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
- **当前无内置 provider**：目录为空时 `connector.provider_not_found` 是常见结果；Notion 的授权/工具错误改由 MCP 错误码表达（如 `mcp.auth_required`、`mcp.server_disabled`），不在本表。

### 5.8 安全与租户边界

- 凭证列读写都不出服务；列表/详情 DTO 明确排除 `credentials`；日志脱敏（token 前缀也不打）。
- 租户隔离继续走 `TenantRepository`；跨租户一律 404。
- 回调只能由 `state` nonce 驱动；nonce 单次消费 + 10 分钟过期 + 绑定 tenant/user/provider/redirect。
- 凭证边界：连接器路径的 provider 凭证（`connections.credentials` 与 `tenant_apps` 应用凭证）只存服务端、不进日志/响应；**Notion 的 OAuth 客户端与 token 现由 agent-orchestrator 的 `mcp_server_auth` 托管**（`AGENT_ORCHESTRATOR__MCP_SECRET` 加密），connector-service 不再持有 Notion 凭证。
- 生产密钥换 KMS/长随机（现状默认值仅供 dev）；P1 补 `CONNECTOR__CREDENTIAL_SECRET` 启动强校验（生产非默认值）。
- 飞书 IP 白名单（历史，provider 已移除）：Serverless 出口 IP 不固定时不可用，v1 未启用；现改为应用级限流 + 错误告警。

## 6. Notion 接入：官方 MCP（2026-11-04 二次变更）

**结论**：Notion 不再由 `connector-service` 内置 provider 承接，改为 **Notion 官方 MCP server**（`https://mcp.notion.com/mcp`），由 **agent-orchestrator** 的 MCP client 统一接入；`/connectors` 页改为「MCP 预设连接器」，内置 `notion` 预设提供一键授权。连接器页是 MCP 的唯一入口：预设卡片在上，页内「我的 MCP 服务」区（锚点 `#mcp-servers`）管理自定义 server；侧栏不再单列「MCP」，`/mcp` 路由仅保留给旧书签与 OAuth `state` 验签失败的兜底回跳。内置 provider 的代码（`connector/providers/notion.py`）、`CONNECTOR__NOTION_*` 配置、`tenant_apps` 的 Notion 用法与相关测试均已删除。

**为什么改**：官方 MCP 已具备完整授权链路——`/.well-known/oauth-protected-resource` 发现（RFC 9728）、AS 元数据、**DCR**（RFC 7591）、**PKCE S256**、`refresh_token`——orchestrator 的 `mcp_oauth` 可直接复用；工具面由 Notion 维护（当前 `tools/list` 约 70 个），平台不需要再为每个能力写 provider 代码与分页/限流/版本适配。`connector-service` 的平台能力保留给**没有官方 MCP**、或需要平台托管凭证/执行面的 SaaS。

**端点与协议能力**：

- 端点：`https://mcp.notion.com/mcp`（Streamable HTTP）；发现与注册走 `mcp_oauth` 现有的 RFC 9728 → 8414 → 7591 流程，无需新代码。
- 无预注册客户端 → **动态客户端注册（DCR）**；授权码 + **PKCE S256**；access token 过期用 `refresh_token` 单飞刷新，刷新失败清空令牌并要求重新授权（加密存 `mcp_server_auth`）。
- 回调：`GET /api/mcp/oauth/callback`（API 网关同源转发，带 `X-Internal-Secret`；`AGENT_ORCHESTRATOR__MCP_OAUTH_REDIRECT_URI` 须与 AS 注册值完全一致）；orchestrator 用 `state`（`MCP_SECRET` 签名，默认 10 分钟）校验后换 token，成功 302 回前端。

**一键授权（`/connectors`）**

1. 页面加载 `GET /v1/mcp/presets` → `{presets:[{key,name,url,description,docs_url}]}`（当前仅 `notion`；`url=https://mcp.notion.com/mcp`，`docs_url=https://developers.notion.com/docs/mcp`）。
2. 卡片「连接 Notion」→ `POST /v1/mcp/presets/notion`：按 `preset.url` 在**本租户**找已有 server，有则原样返回；没有则创建 `transport="http"`、`auto_approve=false`、`tools_allowlist=[]` 的 server（同名回退 `Notion-2`）。幂等，可重复点。
3. `POST /v1/mcp/servers/{id}/oauth/start`，body 可带 `{"next":"/connectors"}`（授权发起页固定用 `/connectors`；`/mcp` 仍被白名单接受，仅供旧直链兼容）；`next` 写进 `state` payload，授权成功后 302 回 `/connectors?oauth=success&server=Notion`（传 `/mcp` 时落 `/mcp?oauth=success`）。
4. 授权成功后**尽力**刷新一次该 server 的 `tools_cache`（失败只记日志，不影响 302）；回到页面后 `GET /v1/mcp/servers` 即可看到 `oauth_authorized=true` 与工具数。
5. 会话侧：输入区「MCP」选择器选中该 server（或深链 `/?mcp=<server_id>`），只用被引用 server 的工具；不选 = 全部已启用 server。

**工具面**

- 以 server 的 `tools/list` 为准（当前约 70 个）；平台命名 `mcp__notion__<tool>`（`<server>` 由 server 名 slug 化），参数 schema 与描述原样来自 server，平台不维护工具白名单（连接器页「我的 MCP 服务」的 server 详情可用 `tools_allowlist` 收窄，默认空 = 全量）。
- `kind=function`，风险档位按 §7 的 annotations/预设规则计算——**读直接执行，写在对话内出确认卡，DANGER 不进对话**。

**配置**

- 需要 `AGENT_ORCHESTRATOR__MCP_SECRET`、`AGENT_ORCHESTRATOR__MCP_OAUTH_REDIRECT_URI`（指向网关 `/api/mcp/oauth/callback`）等 MCP 变量；`connector-service` 不再需要任何 Notion 变量。
- 历史 `CONNECTOR__PROVIDERS_ENABLED` / `CONNECTOR__NOTION_*` 已移除；服务 `Settings(extra="ignore")`，旧 `.env` 里残留不会报错。

**历史归档**：内置 provider 时期的规格（public integration 安装、Basic Auth `users/me` 换 token、`Notion-Version` 头、4 只读 + 2 写工具、静态内部集成令牌连接）保留在 §8.3 的 C6.x 清单与 §9 记录中，仅作历史依据。

## 7. 风险分级与写操作确认（MCP annotations 驱动）

MCP 工具的风险**在工具粒度**上计算（同一 server 可以既有只读也有写），规则按顺序命中（见 `services/agent-orchestrator/agent_orchestrator/services/mcp.py` 的 `_tool_risk`）：

| 顺序 | 条件                                                                                 | 档位       |
| ---- | ------------------------------------------------------------------------------------ | ---------- |
| 1    | `annotations.destructiveHint is True`                                                | **DANGER** |
| 2    | `annotations.readOnlyHint is True`                                                   | **READ**   |
| 3    | `server.url` 命中内置预设（忽略末尾斜杠）且工具名命中该预设任一 `read_prefixes` 前缀 | **READ**   |
| 4    | `server.auto_approve`（租户在连接器页「我的 MCP 服务」打开「免确认执行」）           | **WRITE**  |
| 5    | 其余                                                                                 | **DANGER** |

Notion 预设的只读前缀（`mcp_presets.py`）：`notion-search`、`notion-fetch`、`notion-list-`、`notion-get-`、`notion-query-`、`notion-read-`、`notion-view-`、`notion-download-`、`notion-wait-`、`notion-calendar-list-`。

- `annotations` 随 `tools/list` 落 `tools_cache`（`MCPToolRead.annotations`）；连接器页「我的 MCP 服务」的测试端点刷新缓存后生效，旧缓存没有该字段时按空处理（走第 3 步预设前缀兜底）。
- **annotations 是 server 的自述，不是可信边界**：预设只读前缀只对内置预设生效；非预设 server 仍需租户显式 `auto_approve`（此时非只读工具→WRITE）。「自述 readOnlyHint 的写工具」风险记在 §9.2。

**对话路径**（与 [tool-execution.md]（./tool-execution.md） §7.6 同一条链路）

- 工具箱裁剪为 `kind=function && risk!=danger`：READ 直接执行；WRITE **不执行**——落 `chat_tool_confirmations`（`status=pending`，TTL 10 分钟），SSE `tool` 帧返回 `decision="confirm"`、`ok=false`、`error_code="tool.confirmation_required"`、`confirmation_id`、`expires_at`，并回喂模型「需用户确认，不要重复调用」。
- 前端确认卡点「确认」→ `POST /v1/conversations/{cid}/tool-confirmations/{id}/confirm`，服务端以 `approved=true` 重放同一 `tool_call`；点「取消」→ `.../cancel` 只改状态。
- 错误码：404 `tool_confirmation.not_found`、409 `tool_confirmation.expired`、409 `tool_confirmation.not_pending`、409 `tool_confirmation.tool_unavailable`。
- 确认重放用与发消息**同一套工具箱装配**（allow write、排除 danger），不会出现「看到的工具」与「确认时执行的工具」漂移。
- DANGER 工具不进对话：模型只能收到「该工具需在任务中执行」的提示；任务路径走 `approval_policies` 的 `confirm` 闸，tool-service 的「WRITE/DESTRUCTIVE 无 `approved` → 403」闸门不变（见 [tool-execution.md]（./tool-execution.md） §7.6）。

**默认保守**：预设 server 以 `auto_approve=false` 创建——Notion 上未声明 `readOnlyHint`、也未命中只读前缀的工具一律 DANGER（对话里不执行）；租户在连接器页「我的 MCP 服务」打开「免确认执行」后这些工具降为 WRITE，对话内出确认卡。这比内置 provider 时期「写工具固定 WRITE」**更保守**，属预期行为。

**连接器 provider 写工具（历史）**：内置 provider 时期的 `Notion__create_page` / `Notion__append_blocks` 已随 provider 移除；机制不变——provider 的 `ToolSpec.risk=WRITE` 同样编译成 `risk=write` 并走同一条对话确认链（§5.6）。

**其他 provider / 任意 MCP server（P4）**：把任意 MCP server 并入「连接器」UI 同列展示（`kind=mcp`，凭证仍走 `mcp_oauth`）与组织共享连接仍留 P4；`/connectors` 的旧 provider 目录区只在 `providers.length>0` 时渲染（当前隐藏，旧组件与测试暂不删）。

## 8. 实施清单

估算单位：S ≤ 1 人日，M 2–3 人日，L 4–5 人日。**P0–P2 合计约 25–35 人日**（单人；平台与 provider/前端可并行压缩）。

> **实现状态（2026-11-04 二次回填）**：P1 平台骨架（C1.1–C1.12）仍有效（平台 API / 授权会话 / 刷新巡检 / outbox / 执行面 / 审计），
> 但**内置 provider 已清空**：内置飞书 provider（11-04 上旬移除）与内置 Notion provider（11-04 二次变更移除）均已删除，
> `connector/providers/` 目录为空、`GET /v1/connectors` 返回空目录。Notion 改走**官方 MCP**（§6）：预设 server + 一键授权
>
> - 工具 annotations 风险分级已上线（C7.1–C7.5，见 §8.7），`/connectors` 页改为 MCP 预设连接器（MCP 唯一入口：预设卡片 +「我的 MCP 服务」区；侧栏不再单列「MCP」，`/mcp` 仅保留给旧书签与 OAuth `state` 验签失败兜底回跳）。
>   对话内写操作确认链路（`chat_tool_confirmations`）保留并对 MCP 的 WRITE 工具生效（§7）。
>   与清单的差异：manifest 改为 `BaseProvider` 子类而非 dataclass 注册（§5.6）；执行代理按 provider 寻址
>   （`POST /v1/connectors/{provider}/execute`，§5.3）；租户应用凭证解析落在 `connector/services/apps.py`、
>   巡检在 `connector/services/patrol.py`、后台任务统一由 `ConnectorRuntime` 启停。
>   **历史记录**：C6.x（内置 Notion 专项）与 C3.1/C3.2（内置写工具）保留为历史，随 provider 移除不再适用；
>   对应测试/配置（`CONNECTOR__NOTION_*`、`providers/notion.py`、Notion 专项测试）已删除。
>   **未做/待办（2026-11-04 复核）**：C2.9 只落地最小版——会话内工具失败恢复卡（三类授权错误码 → 「去连接器」，
>   用户重连后手动重发；「+ 菜单」选择与 run 暂停续跑待 C0.4）；C2.10（真实飞书冒烟）随 provider 移除不再适用；
>   P3 剩余（组织共享连接、连接详情授权展示）与 P4（其他 provider、任意 MCP server 并入连接器 UI）未开始。

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
- [x] **C1.4** `POST /v1/connectors/{provider}/authorize` — 验收：返回可用的 provider 授权 URL（Notion 为授权端点 + `state`，不含 PKCE challenge）（S）
- [x] **C1.5** `GET /v1/oauth/{provider}/callback`：换 token → 拉用户信息 → 加密落库 → 302；失败白名单错误码 — 验收：MockTransport 全流程，库里可解出 token，响应不含凭证（M）
- [x] **C1.6** 连接 CRUD 扩展：`PATCH`、`/refresh`、`/check`、详情含 scopes/工具；`DELETE` 补撤销留痕 — 验收：owner 校验、跨租户 404、重复撤销 409（M）
- [x] **C1.7** 刷新单飞：行锁 + 二次检查 + 同事务写回 + 失败置 expired — 验收：并发 10 次刷新只打 1 次上游、只写 1 次库（M）
- [x] **C1.8** 执行代理 `POST /v1/connections/{id}/execute`：工具查找、解密注入、超时、错误映射、usage 日志；内部 token 鉴权 — 验收：token 不出现在响应/日志；超时映射 504；上游 429 带 `retry_after`（M）
- [x] **C1.9** 内部目录 `GET /v1/internal/connectors/tools` — 验收：按租户返回只含 active+enabled 的工具（S）
- [x] **C1.10** 引入 `events` outbox，发 `connector.connection.created/expired/revoked.v1` — 验收：outbox 行 + dispatcher 测试；未启用时降级为纯日志（M）
- [x] **C1.11** 配置与文档：`.env.example`、README「接入新连接器 5 步」更新为 manifest 流程 — 验收：`check-docs` 通过（S）
- [x] **C1.12** 测试：`--cov-fail-under=91`；`pytest-socket` 零网络（全 MockTransport） — 验收：`npx nx test connector-service` 绿（M）

### 8.3 P2 前端 + 执行面 + 内置 Notion（历史；provider 已移除）

> **历史（2026-11-04 二次变更）**：C2.1/C2.2 的飞书 provider 与 C6.1–C6.7 的内置 Notion provider 均已随「Notion 改走 MCP」移除；平台骨架、前端框架、执行面与审计保留（C2.3–C2.8 仍有效），工具面改由 MCP 提供（§6 / §8.7）。

**内置 Notion 专项清单（2026-11-04，已随 provider 移除，仅作历史）**

- [x] **C6.1** `connector/providers/notion.py`：public integration OAuth（HTTP Basic Auth 换 token）、`Notion-Version` 配置化、4 个只读工具 — 验收：MockTransport 走通授权→检索→读页→读块→查询；401→`auth_expired` 且置 expired；429 解析 `Retry-After`（M）
- [x] **C6.2** 授权与身份：`authorize` 不带 PKCE、回调用 Basic Auth 换 token、`GET /v1/users/me` 取 workspace；`account = "{workspace_id}:{owner_subject}"` — 验收：回调落库可解密、同 workspace 多用户各自一条连接、`tenant_apps` 可覆盖 client_id/secret（M）
- [x] **C6.3** 4 个只读工具：`Notion__search` / `Notion__read_page` / `Notion__read_blocks` / `Notion__query_data_source`，`start_cursor`/`page_size` 分页、结果截断、不解析 `cursor`/`url` — 验收：每工具 1 正常 + 1 错误用例；大结果不超字节上限（M）
- [x] **C6.4** 测试：全 MockTransport、零网络覆盖授权/刷新/执行/巡检/outbox/租户应用凭证 — 验收：`uv run pytest` 全绿、覆盖率 ≥ 91%（M）
- [x] **C6.5** 配置与文档：`Settings` / `.env.example` / README / 本文 §6 同步 — 验收：`check-docs` 通过，无飞书配置残留（历史注记除外）（S）
- [x] **C6.6** Notion 写工具 `Notion__create_page`（`POST /v1/pages`）与 `Notion__append_blocks`（`PATCH /v1/blocks/{block_id}/children`），`ToolSpec(risk=ToolRisk.WRITE)`，`risk` 经 catalog 透出到 `/v1/connectors` 的 `tools[].risk` 与内部目录 `ToolDescriptor.risk` — 验收：MockTransport 走通建页/追加；对话路径未确认时不执行（由 orchestrator 确认链路兜底）（M）
- [x] **C6.7** 静态令牌连接：`POST /v1/connections`（`{provider, token, display_name?}` → 201）；Notion `connect_with_token` 用令牌调 `GET /v1/users/me` 校验身份（`account="integration:<bot_id>"`），`auth_kind="token"` + 信封加密落库；目录 provider 项增 `token_connectable`；错误码 400 `connector.token_not_supported` / 400 `connector.invalid_credentials` / 404 `connector.provider_not_found` — 验收：MockTransport 走通「粘贴令牌 → 建连接 → 工具暴露 → 执行」，无效令牌不入库（M）

**原飞书 provider 与前端/执行面条目（历史记录；飞书 provider 已于 2026-11-04 移除）**

- [x] **C2.1**（已移除）`connector/providers/feishu.py`：OAuth（v3 端点）、`user_info`、token 解密/刷新/错误码映射 — 验收：`20037`→expired、`20027`→scope_missing、429 解析 reset 头（M）
- [x] **C2.2**（已移除）P0 六个只读工具 + 分页 + 递归 blocks + 结果截断 — 验收：每工具 1 条正常 + 1 条错误用例；大文档不超字节上限（M）
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

### 8.4 P3 组织共享、授权展示与对话内确认（写工具已完成）

> 2026-11-04 二次变更：C3.1/C3.2 的内置写工具随 provider 移除；对话确认链路本身保留，并对 MCP 的 WRITE 工具生效（§7）。

- [x] **C3.1** Notion 写工具 `Notion__create_page`（`POST /v1/pages`）与 `Notion__append_blocks`（`PATCH /v1/blocks/{id}/children`），默认 `risk=WRITE` + 确认 — 验收：`approval_policies` 命中 confirm；chat 里 `risk=write` 落 pending 确认，用户点击后以 `approved=True` 执行（M）
  - **2026-11-04 完成**：`providers/notion.py` 以 `ToolSpec(risk=ToolRisk.WRITE)` 声明并透出 `risk`；
    对话路径不直接执行，改落 `chat_tool_confirmations` pending（TTL 10 分钟），由
    `POST /v1/conversations/{cid}/tool-confirmations/{id}/confirm` 以 `approved=True` 重放同一 `tool_call`，
    重复确认 409；任务路径继续走 `approval_policies` 的 confirm 闸。实证：connector-service 114 passed / 95.30%、
    agent-orchestrator 946 passed / 87.69%（含 `test_tool_confirmations.py`）。
- [ ] **C3.2** 写工具安全约束：先读后写（page id 来自 `read_page`/`search`）、幂等键用 page id、不解析 `url`/`href`/`cursor` — 验收：重复写不产生重复内容（S）
  - **部分完成（2026-11-04）**：不解析 `url`/`href`/`cursor` 已在 provider 内落实；**幂等键未做**——
    `append_blocks` 重放同一参数会重复追加，确认行的状态机只能挡住「同一 `tool_call` 被重复确认」。
- [ ] **C3.3** 组织共享连接（`owner_subject` 为空 + 授权人留痕）与管理员可见 — 验收：租户内非 owner 可列出但不可撤销（L）
- [ ] **C3.4** 连接详情展示 capabilities 与页面级授权状态（哪些页面已 share 给 integration），capabilities 变更后提示重新授权 — 验收：未 share 资源给出可操作提示而非「重连」（S）

### 8.5 P4 其他 provider 与 MCP 并入（预留）

- [ ] **C4.1** 其他 provider：按 `connector/providers/` 代码 manifest 新增 `BaseProvider` 子类 + `_REGISTRY` 登记 + `CONNECTOR__PROVIDERS_ENABLED` 列出 — 验收：目录出现新 provider，授权/刷新/执行复用平台（M/provider）
- [ ] **C4.2** provider 主动探测：`POST /v1/oauth/introspect` 接入巡检 + 401 自动刷新一次 — 验收：token 失效后巡检自愈，失败才置 expired（M）。（2026-11-04：原为 Notion 专项，provider 移除后暂无对象，能力留待未来 provider。）
- [ ] **C4.3** provider 级独立限流（如 data source 查询约 3 次/秒）与 workspace 共享预算隔离 — 验收：多连接不同时打爆预算（M）。（2026-11-04：原为 Notion 专项，provider 移除后暂无对象。）
- [ ] **C4.4** 任意 MCP server 并入连接器 UI（`kind=mcp`，凭证仍走 `mcp_oauth`） — 验收：审批/审计与原生 provider 一致（L）。（2026-11-04：内置 Notion 预设已并入 `/connectors`，任意 server 并入仍待 P4；MCP 侧的审批/审计已由 MCP client 承接。）

### 8.6 质量与门禁（每期收口）

- [x] **C5.1** `npx nx lint/test connector-service tool-service agent-orchestrator common` 全绿（含覆盖率阈值）
      — 实证（2026-11-04，Notion 写工具与对话内写操作确认合入后）：connector-service 114 passed / 95.30%、
      agent-orchestrator 946 passed / 87.69%、tool-service 343 passed / 89.66%、common 155 passed；
      四个项目 ruff lint 全绿。最新一轮（MCP 预设迁移后）：connector-service 87 passed / 97.71%、
      agent-orchestrator 970 passed / 87.87%、tool-service 343 passed / 89.66%、api-gateway 36 passed。
- [x] **C5.2** `npx nx typecheck/test/lint @beauty/web -- --run` 全绿；`bash scripts/refactor-gate.sh @beauty/web`
      — web 的 lint/typecheck/test/build/docs/workspace 六步全绿（572 个前端用例，含写操作确认卡、确认列表粘性保持与 MCP 预设页）；refactor-gate 的 `py-size` 基线里
      `connector-service/connector/providers/feishu.py` 随 provider 移除不再计入，其余超 500 行文件
      （`tool-service/tests/test_connectors.py`、`agent-orchestrator/tests/test_skill_packages.py`）在 HEAD 上即已超限，
      属既有 W5 ratchet 基线，不由本期引入；留待 W5 体量收敛处理。
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

### 8.7 P5 MCP 预设与 Notion 接入（2026-11-04 二次变更，已完成）

- [x] **C7.1** `mcp_presets.py`：`MCPPreset` + `PRESETS`（当前 `notion`） + `list_presets`/`preset_for_key`/`preset_for_url`（忽略末尾斜杠）；未知 key → 404 `mcp.preset_not_found` — 验收：单测覆盖 key/url 匹配与去尾斜杠
- [x] **C7.2** 工具注解与风险分级：`MCPToolRead.annotations` 透传并写回 `tools_cache`；`_tool_risk` 按 destructiveHint → readOnlyHint → 预设只读前缀 → auto_approve → DANGER 的顺序分档 — 验收：对话路径 WRITE 落确认、DANGER 被裁剪
- [x] **C7.3** 端点：`GET /v1/mcp/presets`、`POST /v1/mcp/presets/{key}`（幂等 ensure，按 url 复用已有 server，重名回退 `Notion-2`）、`POST /v1/mcp/servers/{id}/oauth/start` 接受的 `next`（`/connectors` 为主，`/mcp` 兼容直链）；授权成功尽力刷新 `tools_cache` — 验收：重复调用不重复建 server，回调落点正确
- [x] **C7.4** Web `/connectors` 改为 MCP 预设页：预设卡片、已连接状态与工具数、一键授权、`去会话使用`（`/?mcp=<id>`）、`管理`（本页 `#mcp-servers`，即「我的 MCP 服务」区）；页结构为预设卡片 +「我的 MCP 服务」区 + 旧 provider 目录区（仅 `providers.length>0` 时渲染） — 验收：`nx typecheck/test/lint @beauty/web` 绿
- [x] **C7.5** 文档与部署：本文、tool-execution.md、local-dev.md §3.2、architecture.md、AGENTS.md、README 索引、compose 注释同步；connector-service 删除 Notion provider/配置/测试 — 验收：`check-docs` / `check-workspace` / `compose config` 均通过

## 9. 风险与开放问题

> 2026-11-04 二次变更：下述飞书 / 内置 Notion provider 相关条目保留为历史记录；**当前平台无内置 provider**，Notion 经官方 MCP 接入（预设 + 一键授权 + annotations 风险分级，见 §6 / §7 / §8.7）。

### 9.1 待产品确认（阻塞 P1）

1. **飞书应用交付方式**：每客户自建应用意味着交付时要「建应用 + 配 redirect + 发版审批」，客户管理员参与；是否接受？若要做 SaaS 自助接入，需 ISV 商店应用（资质 + 上架审核 + 放弃长连接）→ 影响 D9。
2. **连接归属**：用户私有（推荐）还是组织共享？共享需要定义谁能撤销、离职如何处理。
3. **首版是否只读**：只读能覆盖「读文档/知识库喂模型」；写文档是否 P1 就要。
4. **执行中授权中断**：v1 是否做「run 暂停→授权→续跑」，还是只做「失败卡 + 手动重试」。
5. **数据边界**：飞书文档内容进入模型与日志的范围、留痕与脱敏要求（是否要内容级审计）。

### 9.2 风险登记

| 风险                               | 影响                                   | 缓解                                                                                              |
| ---------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 飞书 scope 变更需发版 + 管理员审批 | 权限迭代天级，扩能力慢                 | scope 白名单配置化；详情页展示「已授予 vs 期望」；提前规划 P1 scope                               |
| refresh_token 一次性 + 多副本      | 并发刷新导致连接失效                   | 数据库行锁单飞 + 二次检查；巡检任务单实例或加锁                                                   |
| 365 天强制重授权                   | 长期连接必然失效                       | `metadata.refresh_deadline` 提前 30/7 天提示；失效错误码明确                                      |
| docx blocks 递归 + 大文档          | 超时/超大响应                          | 深度与字节双上限；截断标记 + 分页工具                                                             |
| 飞书限流（API×应用×租户）          | 批量读触发 429                         | 令牌桶按三元组 + 读 reset 头退避；错误可重试                                                      |
| 凭证密钥泄露                       | 全部第三方 token 暴露                  | KMS/长随机 + 启动强校验；撤销路径可批量执行                                                       |
| 连接器工具污染模型工具面           | 模型选择困难/误用                      | 工具按连接启用；描述精简；写工具 confirm（任务路径）/ 就地确认（对话路径）；每连接工具数上限      |
| Notion token TTL 未知              | 被动 401，可能影响体验                 | 401 触发刷新 + introspect 巡检；`expires_at` 空表示未知                                           |
| MCP server 自述 annotations 不可信 | 夸大 readOnlyHint 让写工具被当只读执行 | 预设只读前缀只对内置预设生效；非预设 server 需 `auto_approve` 显式信任并出确认卡；DANGER 不进对话 |
| Notion MCP 工具面约 70 个          | 模型选择困难 / 误用                    | 连接器页「我的 MCP 服务」可用 `tools_allowlist` 收窄；预设默认 `auto_approve=false`；写操作确认卡 |

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
- Notion MCP：<https://developers.notion.com/docs/mcp>
- 授权安全：RFC 9700（OAuth 2.0 Security BCP）、RFC 7636（PKCE）
