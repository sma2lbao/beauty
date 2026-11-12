# connector-service

连接器服务(架构 §8,设计见 [docs/design/connector.md](../../docs/design/connector.md)):
把外部 SaaS 账号接入本平台账号,凭证只进不出,并把 provider 能力暴露成 agent 可调用的工具。

**当前仓库没有内置 provider**:连接器服务只提供通用平台(授权会话、凭证托管、
刷新单飞/巡检、目录、执行、连接管理与生命周期事件),Notion 等产品改走 MCP 接入
(见 agent-orchestrator 与前端 `/connectors` 的 MCP 预设连接器)。要加直连 provider
时,按同一套 `BaseProvider` 接口实现并注册即可(步骤见下文「接入新 provider」)。

平台同时支持「粘贴静态令牌建连接」:provider 声明 `token_connectable=True` 并实现
`connect_with_token` 后,用户可跳过 OAuth 直接贴令牌(见下文「静态令牌连接」)。
邮件/日历能力已下线,存量连接在迁移 `b2e4c7a91d33` 中置为 `revoked`;2026-11 按
新规划先后移除了内置的飞书、Notion provider。

除惰性/手动刷新外,还有 **24h 刷新巡检**兜底;连接生命周期事件
(`connector.connection.created/expired/revoked.v1`)经 **outbox** 与状态变更同事务发出。
多账号可各自配置企业应用凭证(`account_apps`),未配置的账号沿用全局应用。详见下文。
测试用 `tests/demo_provider.py`(provider key `demo`,仅测试进程内注册)驱动全链路。

## 端点

浏览器经 API 网关 `/api/connector/v1/...` 访问用户端点;内部端点只给 tool-service,
网关不转发 `/internal`。

| 方法/路径                                  | 说明                                                                                                                            | 鉴权                         |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| `GET /v1/connectors`                       | provider 目录:名称、scope、工具清单(含 risk)、是否配置、是否已连接                                                              | 用户 JWT                     |
| `POST /v1/connectors/{provider}/authorize` | 建一次性授权会话,返回 `{provider, authorize_url, expires_at}`                                                                   | 用户 JWT                     |
| `GET /v1/oauth/{provider}/callback`        | 校验 state → 换 token → 拉身份 → 落库 → 302 回 `/connectors?...`                                                                | 无(一次性 nonce)             |
| `POST /v1/connections`                     | 粘贴静态令牌建连接:`{provider, token, display_name?}` → 201;先校验上游身份再加密落库,`auth_kind=token`                          | 用户 JWT                     |
| `GET /v1/connections`                      | 列表,可按 `provider`/`status`/`enabled` 过滤;**永不返回 credentials**                                                           | 用户 JWT                     |
| `GET /v1/connections/{id}`                 | 详情(跨账号一律 404,不泄漏存在性)                                                                                               | 用户 JWT                     |
| `PATCH /v1/connections/{id}`               | `{display_name?, enabled?}`;关闭启用 ≠ 解绑                                                                                     | 用户 JWT                     |
| `POST /v1/connections/{id}/refresh`        | 手动刷新(行锁单飞);上游失败返回 400 + `error.code`,连接状态留痕                                                                 | 用户 JWT                     |
| `POST /v1/connections/{id}/check`          | 连通性探测;失败不 500,只写 `last_error_code`                                                                                    | 用户 JWT                     |
| `DELETE /v1/connections/{id}`              | 本地撤销(留痕不物理删);仅 active 可撤,重复撤销 409 `connector.invalid_state`                                                    | 用户 JWT                     |
| `POST /v1/connectors/{provider}/execute`   | `{tool, arguments, run_id}`;成功 `{"ok":true,...}`、失败 HTTP 200 + 错误信封,两者顶层都带 `connection_id`(供 tool-service 审计) | `X-Internal-Secret` + 账号头 |
| `GET /v1/internal/connectors/tools`        | 按账号返回可用工具(ToolDescriptor 载荷),供 tool-service 装配                                                                    | `X-Internal-Secret` + 账号头 |

`/healthz`、`/readyz` 为存活/就绪探针(数据库可用即就绪)。

**账号来源**:默认(鉴权开启)校验 Bearer token 的 claim;显式关闭鉴权才认
`X-Account-Id`(仅特殊部署)。内部端点用 `X-Internal-Secret` 鉴权,并用
`X-Account-Id` 显式传账号;secret 留空 = 内部端点整体关闭(401
`connector.internal_forbidden`)。

## 安全与凭证

凭证经应用层信封加密(`connector/core/crypto.py`,Fernet)存 JSON 列,任何读端点
都不返回凭证字段;日志与错误响应只出现错误码。密文密钥走
`CONNECTOR__CREDENTIAL_SECRET`(生产必须换随机长密钥/KMS)。

- **state 只放随机 nonce**:账号/用户/provider/redirect/PKCE verifier 都存在
  `oauth_sessions` 行里,回调按 nonce 一次性消费,10 分钟过期。
- **PKCE S256**:平台为每次授权生成 `code_challenge`/`code_verifier`;支持 PKCE 的
  provider 把它们放进授权 URL 与换 token 请求,不支持 PKCE 的 provider 可在自己的
  `authorize_url`/`exchange_code` 里忽略它们(选哪种换 token 方式由 manifest 决定)。
- **刷新单飞**:`SELECT … FOR UPDATE` 锁连接行 → 锁内二次检查 `expires_at`(并发请求
  只有一个真正换 token)→ 同事务写回;上游 refresh_token 一次性时,这一步是硬要求。
- **错误分层**:只有 `connector.auth_expired` / `connector.oauth_failed` /
  `connector.scope_missing` 会把连接置 `expired`;限流/超时/5xx 是瞬时故障,
  保持 `active` 并写 `last_error_code`,不逼用户重连。
- **账号隔离**走 `database.repository.AccountRepository`;跨账号一律 404。

## 配置

见 [.env.example](.env.example)。核心项:

| 变量                                                                                  | 说明                                                                                                                            |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `CONNECTOR__PROVIDERS_ENABLED`                                                        | 启用哪些 provider(逗号分隔,须已注册);默认空(仓库无内置 provider)                                                                |
| `CONNECTOR__INTERNAL_SECRET`                                                          | tool-service → connector-service 的内部鉴权密钥,留空即关闭内部端点                                                              |
| `CONNECTOR__OAUTH_REDIRECT_BASE`                                                      | `{base}/v1/oauth/{provider}/callback`,须与 provider 后台登记逐字一致;经网关时为 `http://<host>/api/connector`(默认直连 `:8004`) |
| `CONNECTOR__OAUTH_SUCCESS_REDIRECT`                                                   | 授权结束 302 回前端的地址                                                                                                       |
| `CONNECTOR__CREDENTIAL_SECRET`                                                        | 连接凭证加密密钥(生产换随机长密钥/KMS)                                                                                          |
| `CONNECTOR__OUTBOX_ENABLED`                                                           | 是否把连接生命周期事件写 outbox 并后台投递;默认 `false`(降级为结构化日志)                                                       |
| `CONNECTOR__OUTBOX_DISPATCH_INTERVAL_SECONDS` / `..._BATCH_SIZE` / `..._MAX_ATTEMPTS` | outbox 派发轮询间隔(1s)/每批条数(50)/最大尝试次数(5)                                                                            |
| `CONNECTOR__REFRESH_PATROL_ENABLED`                                                   | 是否启动 24h 刷新巡检;生产默认 `true`,测试关闭                                                                                  |
| `CONNECTOR__REFRESH_PATROL_INTERVAL_SECONDS`                                          | 巡检轮询间隔(默认 3600s)                                                                                                        |
| `CONNECTOR__REFRESH_STALE_SECONDS`                                                    | 超过多久没刷新算「过期」候选(默认 86400s = 24h)                                                                                 |

provider 自己的应用凭证字段由该 provider 的 manifest 定义,加在 `Settings`(env 前缀
`CONNECTOR__`)里;未配置时目录里该 provider 的 `configured=false`。

## 静态令牌连接(平台能力)

有些 provider 支持用户直接粘贴静态令牌(内部集成令牌、个人 API token)建连接,
不需要注册 OAuth 应用、也不需要回调。平台把这条路径做成 provider 可声明的能力:

- provider 在 manifest 里声明 `token_connectable: ClassVar[bool] = True`,并实现
  `async def connect_with_token(self, token, client) -> tuple[Credentials, Identity]`:
  先用令牌调上游身份接口校验,返回凭证与身份;校验失败抛 `ProviderError`(鉴权类
  错误统一收敛成 `connector.auth_expired`)。
- 目录 `GET /v1/connectors` 的 provider 项带 `token_connectable`,前端据此显示
  「粘贴令牌连接」入口;`configured=false` 只影响 OAuth 按钮,不影响令牌连接。
- 前端 `POST /v1/connections` 提交 `{provider, token, display_name?}`:服务端先校验
  上游身份,通过后与其它凭证同一套 Fernet 信封加密写库,`auth_kind="token"`。
- `account` 由 provider 决定(如 `integration:<bot_id>`、`token:<账号 id>`);同账号
  同 provider 同 account 重复粘贴**幂等复用同一行**。
- 静态令牌一般没有 `expires_at`/`refresh_token`,不参与惰性刷新与巡检;上游返回
  401 时连接置 `expired`,用户重新粘贴即可。
- 校验失败返回 400 `connector.invalid_credentials`(不区分上游错误码);provider 未
  声明该能力返回 400 `connector.token_not_supported`。

测试参考 `tests/demo_provider.py` 的 `token_connectable` 与 `connect_with_token`,
用例见 `tests/test_token_connect.py`。

## Notion 等产品:改走 MCP

内置 Notion provider(4 只读 + 2 写工具)已于 2026-11-04 移除。Notion 现在通过官方
托管 MCP(`https://mcp.notion.com/mcp`,约 70 个工具)接入:agent-orchestrator 负责
MCP server 的 DCR/PKCE 授权与 `tools/list`,前端 `/connectors` 是 MCP 预设连接器页;
MCP 工具按 annotations 分级(只读直接执行、写操作经对话确认、destructive 拒绝)。
连接器服务与 MCP 互不依赖。

## 接入新 provider

provider 是**代码 manifest + 网络行为**,没有 YAML/builder:

1. 在 `connector/providers/<name>.py` 继承 `BaseProvider`:声明
   `name`/`prefix`/`display_name`/`auth_kind`/`docs_url`/`tools`(每个工具一个
   `ToolSpec`,含 `input_schema`),实现 `authorize_url`、`exchange_code`、
   `refresh`、`fetch_identity`、`call`;只读工具用默认 `risk=read`,写工具在
   `ToolSpec` 上标 `risk=ToolRisk.WRITE`,目录与内部目录都会透出该等级
   (审批由 tool-service/对话侧负责)。要支持「粘贴令牌连接」,再声明
   `token_connectable=True` 并实现 `connect_with_token`。
2. 在 `connector/providers/__init__.py` 的注册表登记构造函数;config 里按需加
   该 provider 的应用凭证字段。
3. 错误统一抛 `ProviderError(code, message, hint, retryable)`;需要重连的错误码
   放 `REAUTH_ERROR_CODES`,上游错误映射参考 `tests/demo_provider.py`。
4. 无需新增路由/表:授权、刷新、目录、执行、连接管理都由平台层复用
   `oauth_sessions` / `connections`(唯一约束 `(account_id, provider, account)`,
   account 写稳定标识,如 provider 的账号 id)。
5. 补测试:上游用 `httpx.MockTransport` 打桩(平台链路参考 `tests/conftest.py`
   的 DemoMock 与 `tests/demo_provider.py`),零网络、零外呼;新 provider 也要覆盖
   授权/刷新/执行/错误映射分支,支持令牌连接时补 `tests/test_token_connect.py`。

## 账号级应用凭证(account_apps)

`account_apps` 每账号每 provider 一行,让不同账号用**自己的**企业应用
(`app_id`/`app_secret`);没配的账号继续用全局 `Settings` 里该 provider 的应用凭证。

- 解析顺序:账号行 → 全局 `Settings`(见 `connector/services/apps.py`);
  行里 `app_id` 与 `app_secret` **都可用**才生效,半配置行记 warning 后回退全局。
- `app_secret_encrypted` 与连接凭证同一套信封加密,payload 约定
  `{"app_secret": "<明文>"}`(兼容历史明文行),密钥同为 `CONNECTOR__CREDENTIAL_SECRET`。
- 授权(发起/回调)、刷新(惰性/手动/巡检)与执行面都按账号解析 provider;
  目录 `/v1/connectors` 的 `configured` 仍用全局应用判断(不暴露账号凭证)。
- v1 无管理 UI/端点,由配置或脚本写入(参考 `tests/conftest.py::insert_account_app`)。

## 连接生命周期事件(outbox)与 24h 巡检

`connector/services/events.py` 定义三个事件,与状态变更**同事务**写 outbox:

| 事件                              | 触发                                           |
| --------------------------------- | ---------------------------------------------- |
| `connector.connection.created.v1` | 首次授权落库(重新授权复用行不重复发)           |
| `connector.connection.expired.v1` | 因需重新授权类失败被置 `expired`(每次失败都发) |
| `connector.connection.revoked.v1` | 用户撤销连接                                   |

payload 至少含 `connection_id`、`provider`、`account`、`status`、`owner_subject`,
不含凭证。`CONNECTOR__OUTBOX_ENABLED=false`(默认)时降级为结构化日志
`connection_event`、不写库;开启后由 `ConnectorRuntime` 起 `OutboxDispatcher`
后台投递(至少一次,消费方按事件 id 去重)。

`connector/services/patrol.py` 的巡检兜住「长期没人调用执行面」的连接:
每 `CONNECTOR__REFRESH_PATROL_INTERVAL_SECONDS` 扫一轮 `active + enabled`
且 `last_refreshed_at` 为空或早于 `now - CONNECTOR__REFRESH_STALE_SECONDS` 的连接,
逐条在独立事务里走与手动刷新相同的单飞路径;单条失败只留痕
(`last_error_code` / `status`)不中断整轮。后台任务由 `connector/core/runtime.py`
统一启停:lifespan 关闭时先停任务再释放 DB/transport(测试两个开关都关,零任务)。

## 迁移

```sh
cd services/connector-service && uv run alembic upgrade head
```

迁移链:`cd993640a7eb`(连接表)→ `b2e4c7a91d33`(下线邮件/日历,存量置
`revoked`,数据清理不可逆、downgrade 为 no-op)→ `8f1c2a5d7b90`(连接生命周期列 +
`oauth_sessions` + 账号应用凭证表)→ `c4f7b1e93a52`
(`outbox_events` + `processed_events`,与 events 包共享模型)→ `4c70941e7a37`
(账号化:账号应用凭证表改名 `account_apps`,各表账号列、唯一约束与索引同步改名)。服务默认 `AUTO_CREATE_TABLES=false`,改表结构必须
配迁移,并在服务目录跑 `uv run alembic check` 确认模型与迁移一致。

## 命令(仓库根)

- `npx nx run connector-service:serve`(:8004)
- `npx nx test connector-service`
- `npx nx lint connector-service` / `npx nx format connector-service`

测试用 `httpx.MockTransport` 打桩示例上游(`--disable-socket`),零网络;覆盖率门禁 91%。
`pyproject.toml` 的 `[tool.coverage.run]` 声明 `concurrency = ["greenlet", "thread"]`:
SQLAlchemy async 经 greenlet 桥接同步 DB 调用,不声明会丢掉 await 之后的帧、把已执行
的服务层代码误报为未覆盖。

## 部署

本服务随仓库在腾讯云轻量服务器（首尔）上用 Docker Compose 部署；Vercel 只承载
`apps/web` / `apps/site` 静态层。编排见
[deploy/tencent/docker-compose.cvm.yml](../../deploy/tencent/docker-compose.cvm.yml)，
方案与验收见 [hybrid 部署方案](../../docs/design/hybrid-deployment.md) 与
[deploy/tencent/README.md](../../deploy/tencent/README.md)。

服务目录不再包含 Vercel 导出配置（`vercel.json` / `.python-version` /
`pyproject.toml` 的 `[tool.vercel]`），这些已于 2026-11-12 随部署主线切换清理。
