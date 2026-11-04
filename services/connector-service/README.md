# connector-service

连接器服务(架构 §8,设计见 [docs/design/connector.md](../../docs/design/connector.md)):
把外部 SaaS 账号连到租户,凭证只进不出,并把 provider 能力暴露成 agent 可调用的工具。

**内置 Notion 连接器**:public integration OAuth(bot token)+ 搜索/读页面/读块/
查 data source 四个只读工具,以及新建页面/追加内容块两个**写工具**(risk=write,
执行前必须经对话确认);平台层(授权、刷新、失效检测、目录、执行、连接管理)
对任何 provider 通用,新 provider 按同一套 `BaseProvider` 接口实现并注册即可(步骤
见下文「接入新 provider」)。邮件/日历能力已下线,存量连接在迁移 `b2e4c7a91d33`
中置为 `revoked`;2026-11-04 按新规划移除了内置的飞书 provider,改以内置 Notion。

除惰性/手动刷新外,还有 **24h 刷新巡检**兜底;连接生命周期事件
(`connector.connection.created/expired/revoked.v1`)经 **outbox** 与状态变更同事务发出。
多租户可各自配置企业应用凭证(`tenant_apps`),未配置的租户沿用全局应用。详见下文。
测试用 `tests/demo_provider.py`(provider key `demo`,仅测试进程内注册)驱动全链路。

## 端点

浏览器经 API 网关 `/api/connector/v1/...` 访问用户端点;内部端点只给 tool-service,
网关不转发 `/internal`。

| 方法/路径                                  | 说明                                                                                                                            | 鉴权                         |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| `GET /v1/connectors`                       | provider 目录:名称、scope、工具清单(含 risk)、是否配置、是否已连接                                                              | 用户 JWT                     |
| `POST /v1/connectors/{provider}/authorize` | 建一次性授权会话,返回 `{provider, authorize_url, expires_at}`                                                                   | 用户 JWT                     |
| `GET /v1/oauth/{provider}/callback`        | 校验 state → 换 token → 拉身份 → 落库 → 302 回 `/connectors?...`                                                                | 无(一次性 nonce)             |
| `GET /v1/connections`                      | 列表,可按 `provider`/`status`/`enabled` 过滤;**永不返回 credentials**                                                           | 用户 JWT                     |
| `GET /v1/connections/{id}`                 | 详情(跨租户一律 404,不泄漏存在性)                                                                                               | 用户 JWT                     |
| `PATCH /v1/connections/{id}`               | `{display_name?, enabled?}`;关闭启用 ≠ 解绑                                                                                     | 用户 JWT                     |
| `POST /v1/connections/{id}/refresh`        | 手动刷新(行锁单飞);上游失败返回 400 + `error.code`,连接状态留痕                                                                 | 用户 JWT                     |
| `POST /v1/connections/{id}/check`          | 连通性探测;失败不 500,只写 `last_error_code`                                                                                    | 用户 JWT                     |
| `DELETE /v1/connections/{id}`              | 本地撤销(留痕不物理删);仅 active 可撤,重复撤销 409 `connector.invalid_state`                                                    | 用户 JWT                     |
| `POST /v1/connectors/{provider}/execute`   | `{tool, arguments, run_id}`;成功 `{"ok":true,...}`、失败 HTTP 200 + 错误信封,两者顶层都带 `connection_id`(供 tool-service 审计) | `X-Internal-Secret` + 租户头 |
| `GET /v1/internal/connectors/tools`        | 按租户返回可用工具(ToolDescriptor 载荷),供 tool-service 装配                                                                    | `X-Internal-Secret` + 租户头 |

`/healthz`、`/readyz` 为存活/就绪探针(数据库可用即就绪)。

**租户来源**:默认(鉴权开启)校验 Bearer token 的 claim;显式关闭鉴权才认
`X-Tenant-Id`(仅特殊部署)。内部端点用 `X-Internal-Secret` 鉴权,并用
`X-Tenant-Id` 显式传租户;secret 留空 = 内部端点整体关闭(401
`connector.internal_forbidden`)。

## 安全与凭证

凭证经应用层信封加密(`connector/core/crypto.py`,Fernet)存 JSON 列,任何读端点
都不返回凭证字段;日志与错误响应只出现错误码。密文密钥走
`CONNECTOR__CREDENTIAL_SECRET`(生产必须换随机长密钥/KMS)。

- **state 只放随机 nonce**:租户/用户/provider/redirect/PKCE verifier 都存在
  `oauth_sessions` 行里,回调按 nonce 一次性消费,10 分钟过期。
- **PKCE S256**:平台为每次授权生成 `code_challenge`/`code_verifier`;支持 PKCE 的
  provider 带上它们,Notion 不支持 PKCE,其 `authorize_url` 忽略 challenge、换 token
  忽略 verifier,改用 HTTP Basic Auth(见下文「内置 Notion provider」)。
- **刷新单飞**:`SELECT … FOR UPDATE` 锁连接行 → 锁内二次检查 `expires_at`(并发请求
  只有一个真正换 token)→ 同事务写回;上游 refresh_token 一次性时,这一步是硬要求。
- **错误分层**:只有 `connector.auth_expired` / `connector.oauth_failed` /
  `connector.scope_missing` 会把连接置 `expired`;限流/超时/5xx 是瞬时故障,
  保持 `active` 并写 `last_error_code`,不逼用户重连。
- **租户隔离**走 `database.repository.TenantRepository`;跨租户一律 404。

## 配置

见 [.env.example](.env.example)。核心项:

| 变量                                                                                  | 说明                                                                                                                            |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `CONNECTOR__PROVIDERS_ENABLED`                                                        | 启用哪些 provider(逗号分隔,须已注册);默认 `notion`                                                                              |
| `CONNECTOR__INTERNAL_SECRET`                                                          | tool-service → connector-service 的内部鉴权密钥,留空即关闭内部端点                                                              |
| `CONNECTOR__OAUTH_REDIRECT_BASE`                                                      | `{base}/v1/oauth/{provider}/callback`,须与 provider 后台登记逐字一致;经网关时为 `http://<host>/api/connector`(默认直连 `:8004`) |
| `CONNECTOR__OAUTH_SUCCESS_REDIRECT`                                                   | 授权结束 302 回前端的地址                                                                                                       |
| `CONNECTOR__CREDENTIAL_SECRET`                                                        | 连接凭证加密密钥(生产换随机长密钥/KMS)                                                                                          |
| `CONNECTOR__NOTION_CLIENT_ID` / `..._CLIENT_SECRET`                                   | Notion public integration 的 OAuth 凭证;未配置时目录里 `configured=false` 且授权接口 409                                        |
| `CONNECTOR__NOTION_VERSION`                                                           | 每个 Notion API 请求带的 `Notion-Version` 头(默认 `2026-03-11`)                                                                 |
| `CONNECTOR__NOTION_API_BASE`                                                          | Notion API/授权基址(默认 `https://api.notion.com`)                                                                              |
| `CONNECTOR__OUTBOX_ENABLED`                                                           | 是否把连接生命周期事件写 outbox 并后台投递;默认 `false`(降级为结构化日志)                                                       |
| `CONNECTOR__OUTBOX_DISPATCH_INTERVAL_SECONDS` / `..._BATCH_SIZE` / `..._MAX_ATTEMPTS` | outbox 派发轮询间隔(1s)/每批条数(50)/最大尝试次数(5)                                                                            |
| `CONNECTOR__REFRESH_PATROL_ENABLED`                                                   | 是否启动 24h 刷新巡检;生产默认 `true`,测试关闭                                                                                  |
| `CONNECTOR__REFRESH_PATROL_INTERVAL_SECONDS`                                          | 巡检轮询间隔(默认 3600s)                                                                                                        |
| `CONNECTOR__REFRESH_STALE_SECONDS`                                                    | 超过多久没刷新算「过期」候选(默认 86400s = 24h)                                                                                 |

provider 自己的应用凭证字段由该 provider 的 manifest 定义,加在 `Settings`(env 前缀
`CONNECTOR__`)里;未配置时目录里该 provider 的 `configured=false`。

## 内置 Notion provider(`notion`)

实现:[`connector/providers/notion.py`](connector/providers/notion.py);Notion 的授权与
API 细节与一般 OAuth provider 不同:

- **授权**:`GET https://api.notion.com/v1/oauth/authorize`,参数含
  `client_id`、`response_type=code`、`owner=user`(固定值)、`redirect_uri`、`state`;
  **不支持 PKCE**,平台生成的 `code_challenge` 被忽略。
- **换 token**:`POST /v1/oauth/token`,**HTTP Basic Auth**(base64 `client_id:client_secret`),
  body 为 `grant_type=authorization_code` + `code` + `redirect_uri`;刷新用
  `grant_type=refresh_token`。token 响应**不含 `expires_in`**,所以 `expires_at` 留空:
  不做按时间惰性刷新,只有 401 才置 `expired` 并要求重连。
- **身份**:`GET /v1/users/me`(bot 身份)+ 换 token 响应里的 workspace 信息,合成
  `account = "{workspace_id}:{owner_subject}"`(owner 为 user 时取 user id,否则
  `workspace`),展示名取用户/工作区名称;`external_workspace` 记工作区名。
- **请求头**:所有 API 请求带 `Authorization: Bearer <token>` 与
  `Notion-Version: {CONNECTOR__NOTION_VERSION}`。
- **只读工具(4 个)**:`Notion__search`(`POST /v1/search`)、`Notion__read_page`
  (`GET /v1/pages/{id}`)、`Notion__read_blocks`(`GET /v1/blocks/{id}/children`)、
  `Notion__query_data_source`(`POST /v1/data_sources/{id}/query`)。Notion 的
  `search` 结果会被压成 id/标题/链接摘要;读块与查库保留原始对象。
- **写工具(2 个,risk=write)**:`Notion__create_page`(`POST /v1/pages`,支持
  `parent_type=page|data_source` + `title` + 段落 `paragraphs`)、
  `Notion__append_blocks`(`PATCH /v1/blocks/{block_id}/children`,`paragraphs`
  每项一段纯文本,`after` 可指定插入位置)。两个工具在目录里透出
  `risk=write`,tool-service 在 `approved` 未置位时以
  `tool.confirmation_required` 拦下;对话侧由「工具卡确认执行」或任务侧危险
  操作确认按钮放行后才真正调用上游。
- **错误映射**:401 → `auth_expired`(需重连);403 → `scope_missing`(页面没共享给集成);
  400 → `invalid_arguments`;429 读 `Retry-After` → `upstream_rate_limited`;
  ≥500 → `upstream_unavailable`(瞬时,不逼重连)。
- **写能力的确认口径**:写工具不自动执行——risk=write 会随
  `ToolDescriptor` 透出,由调用方(对话确认卡 / 任务图确认闸)带
  `approved=true` 才放行;连接器服务本身不做审批判断(设计 §7)。
- **租户凭证**:`tenant_apps` 的 `app_id`/`app_secret` 即 Notion 的
  `client_id`/`client_secret`;未配置的租户沿用全局 `Settings`。

Notion 侧准备:在 <https://www.notion.so/my-integrations> 建 public integration,把
`http://<host>:8004/v1/oauth/notion/callback`(或经网关的
`/api/connector/v1/oauth/notion/callback`)填进 Redirect URIs,把 client id/secret 填进
`CONNECTOR__NOTION_CLIENT_ID`/`CONNECTOR__NOTION_CLIENT_SECRET`;用户授权后还要在
Notion 里把目标页面/数据库 **share 给该集成**,否则只能搜到空结果或 403。
要用写工具,还需在该 integration 的 Capabilities 里勾上 **Insert content** /
**Update content**(只读只需 Read content),否则写入会返回 403/400。

## 接入新 provider

provider 是**代码 manifest + 网络行为**,没有 YAML/builder:

1. 在 `connector/providers/<name>.py` 继承 `BaseProvider`:声明
   `name`/`prefix`/`display_name`/`auth_kind`/`docs_url`/`tools`(每个工具一个
   `ToolSpec`,含 `input_schema`),实现 `authorize_url`、`exchange_code`、
   `refresh`、`fetch_identity`、`call`;只读工具用默认 `risk=read`,写工具在
   `ToolSpec` 上标 `risk=ToolRisk.WRITE`,目录与内部目录都会透出该等级
   (审批由 tool-service/对话侧负责,见「内置 Notion provider」)。
2. 在 `connector/providers/__init__.py` 的注册表登记构造函数;config 里按需加
   该 provider 的应用凭证字段。
3. 错误统一抛 `ProviderError(code, message, hint, retryable)`;需要重连的错误码
   放 `REAUTH_ERROR_CODES`,上游错误映射参考 `tests/demo_provider.py`。
4. 无需新增路由/表:授权、刷新、目录、执行、连接管理都由平台层复用
   `oauth_sessions` / `connections`(唯一约束 `(tenant_id, provider, account)`,
   account 写稳定标识,如 provider 的账号 id)。
5. 补测试:上游用 `httpx.MockTransport` 打桩(平台链路见 `tests/conftest.py` 的
   DemoMock 与 `tests/demo_provider.py`;内置 Notion 的桩与用例见
   `tests/notion_mock.py`、`tests/test_notion_provider.py`),零网络、零外呼;
   新 provider 也要覆盖授权/刷新/执行/错误映射分支。

## 租户级应用凭证(tenant_apps)

`tenant_apps` 每租户每 provider 一行,让不同租户用**自己的**企业应用
(`app_id`/`app_secret`);没配的租户继续用全局 `Settings` 里该 provider 的应用凭证。

- 解析顺序:租户行 → 全局 `Settings`(见 `connector/services/apps.py`);
  行里 `app_id` 与 `app_secret` **都可用**才生效,半配置行记 warning 后回退全局。
- `app_secret_encrypted` 与连接凭证同一套信封加密,payload 约定
  `{"app_secret": "<明文>"}`(兼容历史明文行),密钥同为 `CONNECTOR__CREDENTIAL_SECRET`。
- 授权(发起/回调)、刷新(惰性/手动/巡检)与执行面都按租户解析 provider;
  目录 `/v1/connectors` 的 `configured` 仍用全局应用判断(不暴露租户凭证)。
- v1 无管理 UI/端点,由配置或脚本写入(参考 `tests/conftest.py::insert_tenant_app`)。

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
`oauth_sessions` + `tenant_apps`)→ `c4f7b1e93a52`(`outbox_events` +
`processed_events`,与 events 包共享模型)。服务默认 `AUTO_CREATE_TABLES=false`,改表结构必须
配迁移,并在服务目录跑 `uv run alembic check` 确认模型与迁移一致。

## 命令(仓库根)

- `npx nx run connector-service:serve`(:8004)
- `npx nx test connector-service`
- `npx nx lint connector-service` / `npx nx format connector-service`

测试用 `httpx.MockTransport` 打桩示例上游(`--disable-socket`),零网络;覆盖率门禁 91%。
`pyproject.toml` 的 `[tool.coverage.run]` 声明 `concurrency = ["greenlet", "thread"]`:
SQLAlchemy async 经 greenlet 桥接同步 DB 调用,不声明会丢掉 await 之后的帧、把已执行
的服务层代码误报为未覆盖。

## Vercel 部署（导出约定）

Vercel Python runtime 只自动识别根级 `app.py`/`main.py` 等入口；本服务的 `app` 在
`connector/main.py`，因此由 `pyproject.toml` 的 `[tool.vercel] entrypoint` 显式声明：

- 入口：`connector.main:app`（与 `vercel.json` 的 `functions` key `connector/main.py` 对应）
- Python 版本：`.python-version` 固定 3.12
- 函数配置：`vercel.json` 设 `maxDuration`，并排除 `tests/`、本地 `*.db`、`__pycache__`
  等（Python 包体上限 500MB，Vercel 不做 tree-shaking）

已知阻塞：服务依赖同仓库 uv workspace 内的 `common` / `database` / `events`，而
Vercel 以 `services/connector-service` 为部署根、不包含仓库其他目录；真实部署前需先把这三个包
发布到私有索引或构建期 vendor。本次只覆盖无状态短请求服务，`agent-orchestrator` /
`workspace-service` 含常驻后台循环，按设计文档外置。详见
[serverless 部署方案](../../docs/design/serverless-deployment.md)。
