# connector-service

连接器服务(架构 §8,设计见 [docs/design/connector.md](../../docs/design/connector.md)):
把外部 SaaS 账号连到租户,凭证只进不出,并把 provider 能力暴露成 agent 可调用的工具。

v1 内置**飞书**(企业自建应用 + 用户身份 + 只读能力):授权、刷新、失效检测、目录、
执行与连接管理都已打通;Notion 按同一 provider 接口预留。邮件/日历能力已下线,
存量连接在迁移 `b2e4c7a91d33` 中置为 `revoked`。

## 端点

浏览器经 API 网关 `/api/connector/v1/...` 访问用户端点;内部端点只给 tool-service,
网关不转发 `/internal`。

| 方法/路径                                  | 说明                                                                         | 鉴权                         |
| ------------------------------------------ | ---------------------------------------------------------------------------- | ---------------------------- |
| `GET /v1/connectors`                       | provider 目录:名称、scope、工具清单(含 risk)、是否配置、是否已连接           | 用户 JWT                     |
| `POST /v1/connectors/{provider}/authorize` | 建一次性授权会话,返回 `{provider, authorize_url, expires_at}`                | 用户 JWT                     |
| `GET /v1/oauth/{provider}/callback`        | 校验 state → 换 token → 拉身份 → 落库 → 302 回 `/connectors?...`             | 无(一次性 nonce)             |
| `GET /v1/connections`                      | 列表,可按 `provider`/`status`/`enabled` 过滤;**永不返回 credentials**        | 用户 JWT                     |
| `GET /v1/connections/{id}`                 | 详情(跨租户一律 404,不泄漏存在性)                                            | 用户 JWT                     |
| `PATCH /v1/connections/{id}`               | `{display_name?, enabled?}`;关闭启用 ≠ 解绑                                  | 用户 JWT                     |
| `POST /v1/connections/{id}/refresh`        | 手动刷新(行锁单飞);上游失败返回 400 + `error.code`,连接状态留痕              | 用户 JWT                     |
| `POST /v1/connections/{id}/check`          | 连通性探测;失败不 500,只写 `last_error_code`                                 | 用户 JWT                     |
| `DELETE /v1/connections/{id}`              | 本地撤销(留痕不物理删);仅 active 可撤,重复撤销 409 `connector.invalid_state` | 用户 JWT                     |
| `POST /v1/connectors/{provider}/execute`   | `{tool, arguments, run_id}`;成功 `{"ok":true,...}`,失败 HTTP 200 + 错误信封  | `X-Internal-Secret` + 租户头 |
| `GET /v1/internal/connectors/tools`        | 按租户返回可用工具(ToolDescriptor 载荷),供 tool-service 装配                 | `X-Internal-Secret` + 租户头 |

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
- **PKCE S256**:授权 URL 带 `code_challenge`,换 token 时带 verifier。
- **刷新单飞**:`SELECT … FOR UPDATE` 锁连接行 → 锁内二次检查 `expires_at`(并发请求
  只有一个真正换 token)→ 同事务写回;飞书 refresh_token 一次性,这一步是硬要求。
- **错误分层**:只有 `connector.auth_expired` / `connector.oauth_failed` /
  `connector.scope_missing` 会把连接置 `expired`;限流/超时/5xx 是瞬时故障,
  保持 `active` 并写 `last_error_code`,不逼用户重连。
- **租户隔离**走 `database.repository.TenantRepository`;跨租户一律 404。

## 配置

见 [.env.example](.env.example)。核心项:

| 变量                                | 说明                                                                                                                      |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `CONNECTOR__PROVIDERS_ENABLED`      | 启用哪些 provider(逗号分隔),默认 `feishu`                                                                                 |
| `CONNECTOR__INTERNAL_SECRET`        | tool-service → connector-service 的内部鉴权密钥,留空即关闭内部端点                                                        |
| `CONNECTOR__OAUTH_REDIRECT_BASE`    | `{base}/v1/oauth/{provider}/callback`,须与飞书后台登记逐字一致;经网关时为 `http://<host>/api/connector`(默认直连 `:8004`) |
| `CONNECTOR__OAUTH_SUCCESS_REDIRECT` | 授权结束 302 回前端的地址                                                                                                 |
| `CONNECTOR__FEISHU_APP_ID/SECRET`   | 飞书企业自建应用凭证;未配置时目录里 `configured=false`                                                                    |
| `CONNECTOR__FEISHU_SCOPES`          | 空格分隔,必须含 `offline_access` 才能拿到 refresh_token                                                                   |
| `CONNECTOR__CREDENTIAL_SECRET`      | 连接凭证加密密钥(生产换随机长密钥/KMS)                                                                                    |

## 接入新 provider(如 Notion)

provider 是**代码 manifest + 网络行为**,没有 YAML/builder:

1. 在 `connector/providers/<name>.py` 继承 `BaseProvider`:声明
   `name`/`prefix`/`display_name`/`auth_kind`/`docs_url`/`tools`(每个工具一个
   `ToolSpec`,含 `input_schema` 与 `risk`),实现 `authorize_url`、
   `exchange_code`、`refresh`、`fetch_identity`、`call`。
2. 在 `connector/providers/__init__.py` 的注册表登记构造函数;config 里按需加
   该 provider 的应用凭证字段。
3. 错误统一抛 `ProviderError(code, message, hint, retryable)`;需要重连的错误码
   放 `REAUTH_ERROR_CODES`,上游错误映射参考 `providers/feishu.py`。
4. 无需新增路由/表:授权、刷新、目录、执行、连接管理都由平台层复用
   `oauth_sessions` / `connections`(唯一约束 `(tenant_id, provider, account)`,
   account 写稳定标识,如飞书 `open_id`)。
5. 补测试:上游用 `httpx.MockTransport` 打桩(见 `tests/conftest.py` 的 FeishuMock),
   零网络、零外呼;新 provider 也要覆盖授权/刷新/执行/错误映射分支。

tenant_apps 表已建(按租户覆盖应用凭证),v1 只由配置/脚本写入,解析顺序
「租户行 → 全局 Settings」尚未接入。

## 迁移

```sh
cd services/connector-service && uv run alembic upgrade head
```

迁移链:`cd993640a7eb`(连接表)→ `b2e4c7a91d33`(下线邮件/日历,存量置
`revoked`,数据清理不可逆、downgrade 为 no-op)→ `8f1c2a5d7b90`(连接生命周期列 +
`oauth_sessions` + `tenant_apps`)。服务默认 `AUTO_CREATE_TABLES=false`,改表结构必须
配迁移,并在服务目录跑 `uv run alembic check` 确认模型与迁移一致。

## 命令(仓库根)

- `npx nx run connector-service:serve`(:8004)
- `npx nx test connector-service`
- `npx nx lint connector-service` / `npx nx format connector-service`

测试用 `httpx.MockTransport` 打桩飞书上游(`--disable-socket`),零网络;覆盖率门禁 91%。
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
