# 主流连接器方案调研（Nango / Composio / Arcade / Paragon / Merge / Pipedream / Zapier / Airbyte / MCP / 自建 OAuth）

> 调研时间 2026-11-03；方法：逐家读官方文档（多数站点提供 `.md`/llms.txt 版本，原文留在本地 `/tmp/res/connector/`），本仓库结论另有本地代码取证。
> 可信度标记：**【事实】**＝文档/代码原文可指认（带链接或路径）；**【推断】**＝基于事实的工程判断，未在原文中明确写。
> 面向的落地对象：`services/connector-service`（:8004）、`services/tool-service`（:8008）、`services/agent-orchestrator`（:8002），现状见文末「本仓库现状对照」。

## 0. 一句话结论

**【推断】** 这批产品可以分成三个物种，工程上不要混着抄：

1. **连接/授权基础设施**（Nango、Composio、Arcade、Paragon、Pipedream、Zapier）——核心是「把用户的第三方 OAuth 变成一次安全、可撤销、可刷新的服务端调用」。差异只在抽象粒度：Nango 以「函数（sync/action）」为中心，Composio/Arcade 以「给 agent 的 tool」为中心，Paragon/Pipedream/Zapier 以「嵌入产品的连接门户 + 现成动作」为中心。
2. **统一数据层**（Merge、Airbyte）——把 N 个第三方的数据归一化（Merge 的 Common Model、Airbyte 的 stream/ELT）。**接入飞书/Notion 的 action 场景用不上这层**，成本远超收益。
3. **协议层**（MCP）——规定「agent 如何发现和调用工具、如何做授权」，把授权从应用里剥离成 OAuth 2.1 + PRM/DCR 的标准流程。

对本仓库：**抄 ① 的连接生命周期与凭证托管，不抄 ② 的归一化，把 ③ 当作对外暴露的口径而不是内部实现。**

## 1. 定位速览

| 方案       | 定位                                                      | 对外规模口径【事实】                                | 是否托管第三方 OAuth              |
| ---------- | --------------------------------------------------------- | --------------------------------------------------- | --------------------------------- |
| Nango      | 自托管/云的产品化集成层（auth + 函数执行 + sync）         | 1,000+ API                                          | 是（cloud 或 BYOC 自建）          |
| Composio   | agent 工具运行时（session + 1,700+ toolkit）              | 1,700+ 工具/toolkit                                 | 是（含 customer-managed key）     |
| Arcade.dev | 「actions runtime」+ 自建 MCP server 框架                 | 7,500+ 工具 / 81 个 MCP server                      | 是（token vault + auth provider） |
| Paragon    | 嵌入式 iPaaS（Connect Portal + ActionKit + Managed Sync） | 130+ 原生连接器                                     | 是（完全托管）                    |
| Merge.dev  | Unified API（Common Model）+ Agent Handler(MCP)           | HRIS/ATS/CRM/Accounting/Ticketing/File/KB/Chat 八类 | 是（Merge Link）                  |
| Pipedream  | Connect（组件化 trigger/action + MCP）                    | 3,000+ API / 10,000+ 工具                           | 是                                |
| Zapier     | Platform（app 集成）+ MCP 网关                            | MCP 覆盖其 app 生态                                 | 是（Zapier 持有 app 凭证）        |
| Airbyte    | 数据同步（ELT）连接器生态                                 | 数百 connector（源/目标）                           | 部分（每源一套 OAuth）            |
| MCP        | 协议（tools/resources/prompts + 授权）                    | 无（协议）                                          | 否（由 server 侧实现）            |

来源：<https://nango.dev/docs/getting-started/intro-to-nango.md>、<https://docs.composio.dev/docs/how-composio-works.md>、<https://docs.arcade.dev/llms.txt>、<https://docs.useparagon.com/platform.md>、<https://docs.merge.dev/merge-unified/concepts.md>、<https://pipedream.com/docs/connect.md>、<https://docs.zapier.com/mcp/overview/how-connections-work.md>、<https://docs.airbyte.com/platform/connector-development.md>、<https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture.md>。

## 2. 九维横评

### 2.1 连接器定义方式（manifest / YAML / 代码 / SDK）

- **Nango：代码优先，且已从 YAML 掉头**。【事实】旧方案是 `nango.yaml`（声明 syncs/actions/models），新方案叫 **Zero YAML**：集成 = 纯 TypeScript，用 `defineSync`/`defineAction` 之类的函数对象 + Zod schema 描述模型；**带 `nango.yaml` 的部署会被直接拒绝并指向迁移文档**（<https://nango.dev/docs/guides/platform/migrations/migrate-to-zero-yaml.md>）。这是本调研里最重要的一条反向证据：YAML 声明的连接器在规模变大后维护成本反噬。
- **Arcade：代码 + 装饰器**。工具是 Python MCP server 里的 `@tool(requires_auth=..., scopes=[...])`；auth provider（OAuth 应用）是平台侧配置实体（<https://docs.arcade.dev/en/build/create-tools/tool-basics/create-tool-auth>）。
- **Composio：蓝图与实例分离**。**auth config** 是「这个 toolkit 怎么认证」的蓝图（方法/scope/凭证，全体用户共享），**connected account** 是单用户的实例；默认用 Composio 的 managed app，要品牌/scope/配额时换成自己的 OAuth app（<https://docs.composio.dev/docs/authentication.md>）。
- **Paragon / Pipedream / Zapier：可视化编辑器 + 平台内的 app 定义**。Paragon 侧是项目里「加集成」；Zapier 是 Platform UI 里配 auth 方案（OAuth v2 / API Key / Basic / Digest / Session），Pipedream 是社区维护的 component 代码仓库（`github.com/PipedreamHQ/pipedream/tree/master/components`）。
- **Airbyte：三层**——Connector Builder（低代码 YAML + 表单 UI）、low-code CDK（YAML 描述 global config/user inputs/streams/auth/pagination）、Python CDK（`Stream` 子类 + HTTP stream 基类）。【事实】<https://docs.airbyte.com/platform/connector-development/connector-builder-ui/overview.md>
- **Merge：不支持自定义连接器**。Connector 由 Merge 提供，「你不用建，只需要启用」（<https://docs.merge.dev/merge-agent-handler/connectors.md>）。
- **MCP：不是定义格式**。工具 schema 是运行时通过 `tools/list` 暴露的 JSON Schema，server 自己决定用什么语言/框架实现。

**【推断】** 结论：定义方式与「谁来维护长尾」强相关。自建少量连接器（飞书、Notion）用**代码 + 声明式元数据（manifest 里只放 provider/scope/endpoint/token 策略）**最划算；只有当你要开发生态时才需要 YAML 或可视化 builder。

### 2.2 OAuth 授权码流程、回调、state / PKCE

两种成熟范式，差别在「谁持有浏览器回调」：

- **A. 后端 mint 短令牌 + 托管连接页（Merge / Pipedream / Nango / Composio 都是这个形状）**
  - Merge：后端 `POST /integrations/create-link-token` 拿 `link_token`（有效期 30–720 分钟，Magic Link 可到 10080） → 前端打开 Merge Link → 成功后拿 `public_token` → **后端换永久 `account_token` 存库** → 之后每个请求带 `X-Account-Token`（<https://docs.merge.dev/merge-unified/merge-link.md>）。
  - Pipedream：服务端用 `external_user_id` 生成**短时 Connect token**，前端 SDK 开 iframe；或用 Connect Link（托管页，**4 小时过期**）（<https://pipedream.com/docs/connect/managed-auth/quickstart.md>）。
  - Nango：connect session / 一次性 connect link；授权成功后 Nango 发 **auth webhook 带 connectionId**，后端负责把 connectionId 绑到自己的 user/org——文档明确写 **Nango 不为你的租户关系建模**（<https://nango.dev/docs/guides/auth/auth-guide.md>）。
  - Composio：session 里 `session.authorize()` 生成 Connect Link，或由 agent 用 `COMPOSIO_MANAGE_CONNECTIONS` meta tool 在对话中就地发起授权。
- **B. 直接把 OAuth 回调落在本服务，state 自管（自建 OAuth、Airbyte 每源 OAuth、Arcade 平台侧 auth provider）**
- **MCP 的授权是第三种**：MCP server 对未授权请求回 `401` + **Protected Resource Metadata（RFC 9728）** 指针 → client 发现 AS（OIDC Discovery 或 RFC 8414）→ **预注册或 Dynamic Client Registration** → OAuth 2.1 授权码 + **PKCE** → access token 放 `Authorization` 头。PRM 里公告 `scopes_supported`（示例 `mcp:tools`/`mcp:resources`）（<https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization.md>）。

**【事实】自建 OAuth 的硬性要求**（RFC 9700 OAuth 2.0 Security BCP，2025-01）：授权码一律配 PKCE（RFC 7636）、redirect URI 精确匹配、禁止隐式流、refresh token 轮换或发送方约束、state 一次性且与用户会话绑定（<https://www.rfc-editor.org/rfc/rfc9700>、<https://www.rfc-editor.org/rfc/rfc7636>）。native/桌面端按 RFC 8252 用 loopback 重定向、不发 client secret（<https://www.rfc-editor.org/rfc/rfc8252>）。给 token 绑 audience 用 RFC 8707 resource indicators（<https://www.rfc-editor.org/rfc/rfc8707>）。

**【推断】** state 里**不要放身份**（tenant_id/user_id 放进去等于让回调接受客户端声称的身份）；正确做法是 state 只放一次性的随机 nonce，服务端凭 nonce 查回自己在发起时写下的授权会话行（含 tenant、user、provider、redirect、PKCE verifier、过期时间）。

### 2.3 凭证存储、加密、刷新与并发刷新单飞

- **Nango（公开细节最多，最值得抄）**【事实】：**信封加密**——256-bit DEK 负责加密、DEK 只以 wrapped 形式存放并受 **AWS KMS** 保护；算法 **AES-256-GCM**，每次加密新 12-byte IV + 16-byte auth tag；API key 同样加一层应用级 AES-256-GCM；落库前**脱敏 header**（`Authorization` 恒脱敏 + 该连接的凭证值）；审计保留 1 年；环境之间完全隔离（凭证/连接/集成配置/API key 各自独立）（<https://nango.dev/docs/guides/platform/security.md>）。
- **刷新策略（Nango）**【事实】：自动刷新；**每个 access token 至少 24 小时刷一次**——因为部分 provider 会回收「长期不用」的 refresh token；刷新失败走 webhook 通知、周期性重试，恢复后再发 `success: true`，连续失败若干天才放弃（<https://nango.dev/docs/guides/auth/token-refreshing.md>）。
- **Composio**：AES-256-GCM at rest + TLS；**token 不出 API**——Connected Account 的 Get/List 在 API 边界默认脱敏（managed 与 custom auth config 都适用）；需要打未封装 endpoint 时用 **Proxy Execute**，且限「与已连接账号 base URL 同 eTLD+1」；Proxy 需要独立 API key 权限；私有 VPC / 自托管 / **customer-managed keys**（你云里的 proxy 用你的 KMS 解密，Composio 不持密钥）（<https://docs.composio.dev/docs/security/token-custody.md>、<https://docs.composio.dev/docs/extending-sessions/proxy-execute.md>）。
- **Arcade**：自托管架构里有一个独立 **token vault** 服务：走完 OAuth、加密存 token、过期前刷新、按调用注入给 Engine；LLM 与 MCP client 都看不到 token（`context.get_auth_token_or_empty()`）（<https://docs.arcade.dev/en/operate/deploy/architecture>）。
- **Paragon / Pipedream / Zapier**：均为「凭证加密存储 + 到期自动刷新」，细节不公开（Pipedream 明确 HTTPS 传输 + 静态加密、模型/客户端侧永不暴露）。
- **并发刷新单飞**：**没有一家在文档里公开讲实现**。【推断】标准做法是 per-connection 互斥（DB 行锁 `SELECT ... FOR UPDATE` 或 Redis 锁）后二次检查 `expires_at`，刷新结果（尤其轮换后的 refresh token）与 `expires_at` 同一事务写回；多实例部署时锁必须落在共享存储而不是进程内。

### 2.4 多租户与租户隔离

- **Composio 讲得最细**【事实】：B2B 先决定连接归谁——**member-owned**（`org_42:user_7`）还是 **workspace-owned**（`org_42:integration`，服务账号，能扛成员变动）；警告不要把成员塌缩成租户 ID；workspace-owned 时「谁能驱动这个 agent」由你的应用自己校验（角色/成员关系）；shared connections 是**实验特性**，不要当默认租户模型；webhook 回调也要做租户/角色校验（<https://docs.composio.dev/docs/b2b-agents.md>）。
- **Nango / Paragon / Pipedream**：都是「**平台侧只存连接，租户映射在调用方**」。Nango 要你持久化 connectionId；Paragon 用 SDK token 认证你的客户；Pipedream 用 `external_user_id` 标识终端用户。Merge 的 `account_token` 同理。
- **Arcade**：org / project / 成员角色 + API key 的分层，token vault 按 user 维度存。
- **MCP**：租户隔离不在协议里，由 server 的 token 校验 + 资源归属实现；规范专门警告 **token passthrough**（把 client 拿到的 token 直接透传给上游）与 **confused deputy**（<https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices.md>）。

**【推断】** 对本仓库的直接含义：`TenantMixin` + `TenantRepository`（跨租户 404）已经把边界放在数据访问层，这是对的；**要补的是「连接归属 principal」这一维**——同租户内是「用户私有连接」还是「组织共享连接」，必须显式建模，否则撤销与审计说不清是谁授权的。

### 2.5 webhook / 事件订阅与增量同步

- **连接生命周期事件是标配**：Nango 授权成功/刷新失败/恢复 webhook；Pipedream `CONNECTION_SUCCESS`/`ERROR`（**payload 不含凭证**，只给 `account.id`）；Merge 双 webhook（Merge 自己的数据变更 + 第三方 webhook 透传）。
- **数据增量同步是一整套重活**：Nango 有 records cache、checkpoints（断点续跑转增量）、deletion detection、realtime syncs（webhook + 轮询兜底）、sync partitioning（一个连接内按 mailbox/folder/channel 分区并行）；Paragon Managed Sync 提供全量 + 增量 + 失败恢复；Merge 用 `modified_after` + 游标分页 + 定时 sync；Airbyte 的整套协议就是 state（cursor）驱动的 stream 同步。
- **共同模式**：【推断】首轮全量 → 存 cursor/checkpoint → 之后按 cursor + `modified_after` 增量 → webhook 只做「提前触发一次增量」的加速器，**不能作为唯一数据源**（会丢事件）→ 定期兜底全量对账。

### 2.6 连接器能力如何暴露给 LLM / agent

- **Composio：session + tool router**【事实】。session 绑定 user/toolkits/auth/connected accounts，默认给 agent 的是**元工具**（搜索工具、管理连接、执行），而不是把几百个 tool 定义塞进上下文；带沙箱处理大响应；工具调用以具体 user 的 connected account 执行（<https://docs.composio.dev/docs/how-composio-works.md>）。
- **Arcade：工具即授权点**。`requires_auth` 声明 provider+scopes，调用时若 scope 不足就抛「OAuth challenge」给用户，授权后重试；token 注入 `Context`，**客户端和模型永远看不到**（<https://docs.arcade.dev/en/build/create-tools/tool-basics/create-tool-auth>）。
- **Merge Agent Handler：MCP 网关形态**。bearer token 解析出 org + **Tool Pack**（工具包）+ Registered User；工具名在 wire 上命名空间化为 `<Connector>__<tool>`（如 `slack__post_message`），避免两个连接器概念撞名；调用前过安全/DLP 扫描、调用中按需刷新 OAuth、调用后落审计（<https://docs.merge.dev/merge-agent-handler/how-it-works.md>）。
- **Nango**：把 action function 暴露为带 schema 的 tool + MCP；提供「agent sessions」作为 agent 专用接口。
- **Pipedream**：每个 app 一个专属 MCP server，tool 由 registry 的 action 自动生成。
- **MCP 协议本身**：tools（可调用动作）、resources（可读数据，URI 寻址，供 @ 引用/附件式上下文）、prompts（模板）；三者分离是重要设计——**「数据引用」和「动作」不该混成一个 tool**（<https://modelcontextprotocol.io/docs/2026-07-28/learn/server-concepts.md>）。

**【推断】** 暴露粒度的取舍：工具少（<20）时全量注入 schema 最省事；到几十上百个就必须像 Composio/Merge 那样加一层「tool 搜索/命名空间 + 按需加载」，否则上下文和误选率都失控。本仓库已有 `mcp__<server>__<tool>` 命名空间约定（`docs/architecture.md` §11.2），方向一致。

### 2.7 权限范围、审计、撤销

- **scope 最小化**：Arcade 按 tool 声明 scopes 并在调用时校验；Composio 在 auth config 定 scope 并用 session 配置限制可调 toolkit/tool；Nango 的 suggested scopes 是自动发现并每月刷新的建议值（可选任意 provider 支持的 scope）。
- **审计**：Arcade 的 audit log **默认开启**，控制台可筛 + REST API（可按 org/project 拉取），可导入 SIEM；**每个工具调用的输入输出对项目管理员可见**；Nango 审计记录 actor/结果/来源 IP，保留 1 年；Merge Agent Handler 每次调用有审计记录；Composio 有 Logs。
- **撤销**：Paragon/Pipedream/Zapier 都是用户可在门户撤销；Pipedream 强调「随时可撤销」；Nango 的删除是**软删**（连接立刻不可用、sync 停止、定时任务取消），日志与审计按各自保留期走；重连应**更新同一 connection**而不是删了重建（否则丢数据/丢配置）（<https://nango.dev/docs/guides/platform/security.md>、<https://nango.dev/docs/guides/auth/auth-guide.md>）。
- **策略/审批**：Arcade 有 **contextual access webhook**（`/pre` `/post` `/access`）在工具调用前后做策略判定；Merge Agent Handler 有 DLP 扫描层。

### 2.8 自托管 vs SaaS、计费【事实/推断混合】

- **可自托管**：Nango（self-hosted / BYOC；Enterprise self-hosted 标注「暂不可用/计划中」）、Arcade（K8s Helm，cloud 或 on-prem）、Composio（VPC/self-hosted/customer-managed key）、Airbyte（OSS self-hosted vs Cloud）。**纯 SaaS**：Paragon、Merge（Agent Handler 提供 MCP 托管）、Pipedream、Zapier。
- **计费维度各不相同**（口径为各家定价页常见口径，**本次未逐页核对数字**）：按活跃连接数（Nango）/ 按 tool call 或 credits（Composio、Arcade）/ 按 task（Zapier）/ 按 credits（Pipedream）/ 按连接的月费或按 sync 数据量（Merge、Paragon）。【推断】计费维度决定了产品内部必须埋的计量点（`run_events` 之于本仓库）。

### 2.9 典型数据模型

| 方案      | 定义层                                         | 连接层                                                         | 凭证/令牌                         | 其它                                   |
| --------- | ---------------------------------------------- | -------------------------------------------------------------- | --------------------------------- | -------------------------------------- |
| Nango     | Integration（`provider_config_key`）+ Function | Connection（id、config、metadata、tags）                       | 加密存 Connection                 | Sync records、checkpoints、环境        |
| Composio  | Toolkit + AuthConfig（蓝图）                   | ConnectedAccount（user 维度）                                  | 加密存 ConnectedAccount，不出 API | Session、Tool、Trigger                 |
| Arcade    | AuthProvider（provider 配置）+ Tool            | User/Org/Project 下的 token vault 条目                         | vault 加密存储                    | Gateway、MCP server、audit log         |
| Paragon   | Integration（connector）                       | ConnectedUser / connection（每客户）                           | 加密存储（平台托管）              | Workflow、Sync job、Action             |
| Merge     | Connector（启用即可）                          | LinkedAccount（`account_token`）                               | 凭证在 Merge                      | Common Model、sync、webhook、Tool Pack |
| Pipedream | App + Component（trigger/action）              | ConnectedAccount（`external_user_id`）                         | 加密存储                          | Connect token、MCP server              |
| Zapier    | App 集成（auth scheme + trigger/action）       | Connection（用户账号）                                         | Zapier 持有                       | Zap、MCP server、task 计量             |
| Airbyte   | ConnectorDefinition（源/目标）                 | **Connection = source→destination 的同步管道**（同名不同义！） | 每源配置里的 OAuth                | Stream、state/cursor、schedule         |
| MCP       | Server                                         | ——（协议不管持久化）                                           | 由 AS 签发、client 持有           | Tool / Resource / Prompt               |

### 2.10 自建 OAuth 的常见模式（不依赖上述厂商时）

1. **一次性授权会话行**：发起时在库里写 `(id, tenant_id, user_id, provider, redirect_uri, code_verifier, state_hash, expires_at)`，state 只带 `id`；回调按 id 查行并校验过期/一次性。【推断】
2. **PKCE + 精确 redirect 白名单**（RFC 9700）；桌面端走 loopback（RFC 8252），不给 client secret。
3. **code → token 交换必须在后端**；前端只拿「已连接/未连接」状态，永远拿不到 access/refresh token。
4. **refresh token 轮换**：每次刷新拿新 refresh token 就旧的作废；刷新写回要原子（行锁 + 事务），否则并发刷新会互相把对方的 token 作废——这是自建 OAuth 最常见的线上事故。【推断】
5. **token 短命 + 每次调用前检查**（剩余寿命 < N 分钟即刷新），并对失效连接置 `expired`、发事件让 UI 出「重新连接」。【推断】

## 3. 对接入本仓库最值得借鉴的 5 条

> 现状：`services/connector-service` 已有连接表 + 信封加密 + 租户隔离 + 撤销（`services/connector-service/connector/models/connections.py`、`connector/core/crypto.py`、`connector/services/connections.py`），**但没有创建连接端点、没有 OAuth 流程、没有刷新逻辑**（`services/connector-service/README.md` 明说）。

1. **定义层与连接层分离，定义用代码里的静态 manifest，不用 YAML。**
   `connector/manifests/feishu.py` 之类的一个 dataclass 声明 provider 元数据（auth 类型、endpoint、scopes、token 刷新策略、可执行能力列表），连接行只存 `tenant/provider/account/status/credentials/expires_at`。参考 Composio 的 AuthConfig vs ConnectedAccount、Nango 的 Integration vs Connection。**理由**：Nango 自己已废弃 `nango.yaml` 转纯 TS（迁移文档明确拒绝 YAML 部署），说明声明式配置在长尾里反而变成负担；本仓库 Python 直接写代码成本最低。代价：小（新增 manifest 协议 + 注册函数）。

2. **OAuth 用「后端 mint 短令牌 + 一次性授权会话」，回调只认服务端状态。**
   新增 `POST /v1/oauth/sessions`（body: provider；从 JWT 取 tenant/user；落一行带 `state_hash`/`code_verifier`/`expires_at` 的会话）→ 前端打开托管页 → `GET /v1/oauth/callback?code&state` → 校验后换 token、写 `connections`、发 outbox 事件。参考 Merge 的 link_token/public_token/account_token、Pipedream 的 Connect token（4h）、Nango 的 connect session。**理由**：把「谁在授权」的真相留在服务端，避免信任客户端传来的 tenant；PKCE 与精确 redirect 是 RFC 9700 的硬要求。

3. **凭证零出口：能力一律经 connector-service 代理执行，tool-service 只见结果。**
   增加 `POST /v1/connections/{id}/execute`（或 `/proxy`）签名，内部解密凭证 → 调第三方 → 返回数据；`ConnectionRead` 保持无凭证字段（已有）。参考 Composio Proxy Execute（token 不出 API、限制同域）与 Arcade token vault 注入。**理由**：本仓库已有 `crypto.py` 的 `encrypt_credentials`/`decrypt_credentials` 与「读端永不返回凭证」的 schema 约定，继续把「解密点」收敛到单一出口，token-service/agent-orchestrator 就不会各自拿到明文凭证。代价：中（要把 HTTP 执行信封与 `ToolDescriptor.executor=http` 对齐，见 `packages/common/common/tools.py`）。

4. **刷新做单飞 + 主动保活，失败落状态与事件。**
   per-connection 行锁（`SELECT ... FOR UPDATE` 或 advisory lock）+ 二次检查 → 提前 N 分钟刷新 → 事务内写回 `credentials`/`expires_at`；失败把 `status` 置 `expired`（枚举已存在）并复用 `events.outbox` 发事件供 UI/通知消费；对长期不用的连接做 Nango 式 24h 保活刷新。**理由**：并发刷新 + refresh token 轮换是自建 OAuth 的头号坑；本仓库已有 outbox 与 `status=expired`，缺的只是刷新循环与锁语义。

5. **连接器能力用现有 ToolDescriptor 暴露，风险分级交给 approval_policies，审计落 run_events。**
   每个 manifest 能力生成一个描述符（`ToolRisk` 只读/写/破坏、`ExecutorKind.HTTP`、输入 schema），命名沿用 `mcp__<server>__<tool>` 或 `feishu__send_message` 风格；写操作默认 `confirm`；执行必写 `run_events`。参考 Merge 的 `<Connector>__<tool>` 命名空间、Arcade 的 `requires_auth` + scopes、`docs/architecture.md` §11.2 已有的 per-tenant/per-connector/per-tool `auto|confirm|deny` 策略。**理由**：不引入新抽象就能让连接器同时被 chat、run 编排和对外 MCP server（§8.2）复用。

## 4. 不建议照搬的点

1. **不要抄 YAML/可视化 builder**（Nango `nango.yaml`、Airbyte low-code YAML、Zapier Platform UI）。前者已被 Nango 自己废弃，后者是数据同步/生态产品形态，本仓库只有两个内置连接器，收益为零、维护成本正增长。
2. **不要抄 Unified API / Common Model**（Merge、Paragon 的归一化）。归一化（HRIS/CRM/Accounting 八类）是为「同类数据跨 N 个 provider」设计的；飞书/Notion 各自的对象模型差异大且只有两家，抽象层会先于需求出现。
3. **不要把凭证托管外包给 SaaS**（Nango cloud / Composio cloud / Paragon / Merge）。已有租户隔离、加密基座与内网/离线部署约束（`docs/architecture.md` §10.3），外部托管会引入数据出境与合规评审成本。**可借鉴其机制，不采购其托管。**
4. **第一版不要做数据同步引擎**（Nango syncs/checkpoint/deletion detection、Paragon Managed Sync、Merge 定时 sync）。webhook 只做**连接生命周期事件**（授权成功/刷新失败/撤销）；内容同步等有真实需求再立项，否则会背上 cursor/去重/对账三座山。
5. **不要抄 Arcade「同类型多 auth provider 需要 fork 工具」的设计**。Arcade 多个 Google provider 时必须改工具代码指定 provider id（<https://docs.arcade.dev/en/references/auth-providers>）。manifest 里 provider 应是唯一 ID，能力直接引用它，天然没有歧义。
6. **不要为内部 agent 引入 MCP 的 DCR/PRM 全套**。那套流程是给「任意第三方 MCP client 接入你的 server」用的；本仓库对外 MCP server 已有自建 IdP + JWT/JWKS，内部调用直接走服务间契约更简单。MCP 的 `tools/resources` 分离与「token passthrough 禁止」两条值得抄，DCR 不值得。

## 5. 附：本仓库现状对照（写结论时的取证）

| 已有能力                                                                                                                          | 位置                                                                                              | 与本调研的关系                                              |
| --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- | ---------------------------- | --------------------- |
| 连接表：`UniqueConstraint("tenant_id","provider","account")`、`status` active/revoked/expired、`credentials` JSON                 | `services/connector-service/connector/models/connections.py`                                      | 对应 2.9 的「连接层」，缺 `expires_at` 与归属 principal     |
| 信封加密：`{"v":"1","ciphertext":"fernet:<token>"}`，`SecretBox`，历史明文回退，错误码 `connector.credentials_unreadable`         | `services/connector-service/connector/core/crypto.py`                                             | 对应 2.3 的「应用层信封加密」，缺 KMS 与 key rotation       |
| 密钥配置 `CONNECTOR__CREDENTIAL_SECRET`（默认值 `dev-connector-secret-change-me`）                                                | `services/connector-service/connector/core/config.py`                                             | 生产必须换 KMS/随机长密钥（README 已注明）                  |
| 读端点无凭证字段、跨租户 404、撤销软状态 + 409 `connector.invalid_state`                                                          | `services/connector-service/connector/api/v1/connections.py`、`connector/services/connections.py` | 对应 2.7 的撤销与「凭证只进不出」                           |
| 工具契约：`ToolRisk` read/write/destructive、`ToolKind` function/interaction、`ExecutorKind` sandbox/http/none、`{"ok":...}` 信封 | `packages/common/common/tools.py`                                                                 | 第 5 条借鉴的落点                                           |
| per-tenant/per-connector/per-tool 的 `auto                                                                                        | confirm                                                                                           | deny` 审批、`mcp**<server>**<tool>`命名、调用写`run_events` | `docs/architecture.md` §11.2 | 第 5 条借鉴的落地机制 |

## 6. 来源清单（均于 2026-11-03 访问）

- Nango：<https://nango.dev/docs/getting-started/intro-to-nango.md>、<https://nango.dev/docs/guides/auth/auth-guide.md>、<https://nango.dev/docs/guides/auth/token-refreshing.md>、<https://nango.dev/docs/guides/platform/security.md>、<https://nango.dev/docs/guides/platform/migrations/migrate-to-zero-yaml.md>、<https://nango.dev/docs/guides/agent-sessions.md>、<https://nango.dev/docs/guides/functions/syncs/sync-functions.md>
- Composio：<https://docs.composio.dev/docs/how-composio-works.md>、<https://docs.composio.dev/docs/authentication.md>、<https://docs.composio.dev/docs/security/token-custody.md>、<https://docs.composio.dev/docs/b2b-agents.md>、<https://docs.composio.dev/docs/extending-sessions/proxy-execute.md>
- Arcade：<https://docs.arcade.dev/en/build/create-tools/tool-basics/create-tool-auth>、<https://docs.arcade.dev/en/references/auth-providers>、<https://docs.arcade.dev/en/operate/deploy/architecture>、<https://docs.arcade.dev/en/operate/governance/audit-logs>
- Paragon：<https://docs.useparagon.com/platform.md>、<https://docs.useparagon.com/connect-portal/overview.md>、<https://docs.useparagon.com/learn/actionkit.md>、<https://docs.useparagon.com/learn/managed-sync.md>
- Merge：<https://docs.merge.dev/merge-unified/concepts.md>、<https://docs.merge.dev/merge-unified/merge-link.md>、<https://docs.merge.dev/merge-unified/reading-data/webhooks/overview.md>、<https://docs.merge.dev/merge-agent-handler/how-it-works.md>、<https://docs.merge.dev/merge-agent-handler/connectors.md>
- Pipedream：<https://pipedream.com/docs/connect.md>、<https://pipedream.com/docs/connect/managed-auth/quickstart.md>、<https://pipedream.com/docs/connect/components.md>、<https://pipedream.com/docs/connect/mcp.md>、<https://pipedream.com/docs/connect/webhooks.md>
- Zapier：<https://docs.zapier.com/mcp/overview/how-connections-work.md>、<https://docs.zapier.com/integrations/build/auth.md>、<https://docs.zapier.com/integrations/build/oauth.md>
- Airbyte：<https://docs.airbyte.com/platform/connector-development.md>、<https://docs.airbyte.com/platform/connector-development/connector-builder-ui/overview.md>、<https://docs.airbyte.com/platform/connector-development/cdk-python/basic-concepts.md>、<https://docs.airbyte.com/platform/connector-development/cdk-python/incremental-stream.md>
- MCP：<https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization.md>、<https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices.md>、<https://modelcontextprotocol.io/docs/2026-07-28/learn/server-concepts.md>、<https://modelcontextprotocol.io/docs/2026-07-28/operate/...>（架构页 <https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture.md>）
- 自建 OAuth 规范：<https://www.rfc-editor.org/rfc/rfc9700>、<https://www.rfc-editor.org/rfc/rfc7636>、<https://www.rfc-editor.org/rfc/rfc8252>、<https://www.rfc-editor.org/rfc/rfc8707>、<https://www.rfc-editor.org/rfc/rfc9728>
