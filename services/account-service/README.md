# account-service

账号服务(架构 §3):**账号 / 配额 / usage / API Key + sessions/devices**,
以及**自建认证**(注册/登录/刷新,由阶段 A 的 dev IdP 桩转正,§10/§11)。

**隔离边界就是账号**:一个账号一行 `users`,主键 `users.id`(32 位 hex)即
`account_id`,也是所有账号私有数据与 JWT 的 `account_id` claim。没有组织、
成员、邀请或套餐产品层——账号即个人空间(ChatGPT 个人模型);管理面只用
`users.role`(`user`/`admin`)区分权限,角色**不进 JWT**(每请求读库,见下)。

- `GET /v1/me` / `PATCH /v1/me`:当前账号档案,返回
  `AccountRead{id,email,display_name,status,role,created_at}`;PATCH 只改
  `display_name`(显式 `null` 回落到 email 前缀),邮箱/状态由认证流程维护;
- `POST /v1/sessions`、`GET /v1/sessions`、`POST /v1/sessions/{id}/revoke`:
  多端会话/设备登记与吊销(app 预留,§7);只能看到/吊销自己账号的会话;
- `/healthz` `/readyz`:存活/就绪探针。

所有账号面端点从 Bearer token 的 `account_id` claim 取账号;跨账号访问一律
404,不泄漏存在性。

## 自建认证(始终挂载)

本服务兼作 IdP:凭证校验(密码)+ 令牌签发(RS256)+ OIDC 发现/JWKS,
兄弟服务用同一个 `JwtVerifier` 经 HTTP 发现自动验签——**不设任何后门**,
将来要换 Logto 等托管 IdP 只改各服务 `AUTH_ISSUER`,代码零改动。

- `POST /v1/auth/register`:注册即建账号(`subject` = email,全局唯一;
  409 `account.email_taken`);密码最小 8 位,argon2id 哈希。响应是**扁平**
  `TokenResponse{access_token,refresh_token,token_type,expires_in,account_id,subject,session_id}`;
- `POST /v1/auth/login`:邮箱 + 密码换**双令牌**;失败统一 401,不区分
  "账号不存在"与"密码错"(防枚举;账号不存在时也跑一次假哈希校验);
- `POST /v1/auth/refresh`:**旋转式刷新**——旧 refresh token 一次性,用后即
  作废并签新对;已作废令牌被再次使用视为**泄露信号**,吊销该账号全部
  刷新令牌;库中只存 sha256,令牌本体 = 256 位随机 urlsafe;
- `POST /v1/auth/logout`:吊销指定 refresh token(幂等);access 不设黑名单,
  自然过期(默认 1 小时);
- `POST /v1/auth/issue`:**内部服务签发**(服务间调用用,`X-Internal-Secret`
  匹配 `INTERNAL_SECRET` 才放行,密钥未配置即端点关闭):请求体
  `{"account_id": str, "subject": str}`,响应
  `IssueResponse{access_token,token_type,expires_in,account_id,subject}`;
  账号不存在或非 `active` → 401 `auth.account_missing`——orchestrator 等下游
  服务经它与用户面走**同一个** JwtVerifier;
- `GET /.well-known/openid-configuration` + `GET /jwks`:OIDC 发现文档与 JWKS。

### 配置(前缀 `ACCOUNT__`)

| 变量                  | 默认       | 说明                                                                                   |
| --------------------- | ---------- | -------------------------------------------------------------------------------------- |
| `TOKEN_TTL_SECONDS`   | 3600       | access token 有效期                                                                    |
| `REFRESH_TTL_SECONDS` | 2592000    | refresh token 有效期(30 天)                                                            |
| `TOKEN_AUDIENCE`      | beauty-api | JWT `aud`(与各服务 `AUTH_AUDIENCE` 一致)                                               |
| `SIGNING_KEY_PATH`    | 空         | 签名私钥 PEM 路径:设置则跨重启不轮换 kid(生产必设);留空则进程内生成(dev/test)          |
| `INTERNAL_SECRET`     | 空         | 内部端点(`/v1/auth/issue`、`/usage/events`、`/byok/*` 内部面)的共享密钥,留空即关闭     |
| `AUTH_ENABLED`        | true       | 校验 Bearer 并从 token claim 解析账号(自建 IdP 即本服务;显式关闭走 `X-Account-Id` 头,仅特殊部署) |
| `ADMIN_EMAILS`       | []         | 管理员引导名单(JSON 数组,大小写不敏感);注册/登录命中即持久化 `role=admin`,鉴权时也兜底放行 |

## 管理面(RBAC / 审计)

管理员 = `users.role='admin'` **或**邮箱命中 `ADMIN_EMAILS`(兜底);两种判定
都**每请求读库**(access token 里仍只有 `account_id`,权限变更即时生效,最长
延迟只是 access token 的 1h 生命周期本身)。

引导第一个管理员:把邮箱写进 `ACCOUNT__ADMIN_EMAILS`(JSON 数组)并重启,
该邮箱注册/登录后 `role` 被持久化为 `admin`;随后建议用
`PATCH /v1/admin/accounts/{id}/role` 显式授权,再从名单移除(名单只升不降——
移出名单不会自动降权,先降 `role` 再移)。

- `GET /v1/admin/accounts`:账号列表(`q` 对 email/display_name 大小写不敏感包含、
  `status` 过滤、`limit` 1..100 默认 20、`offset`;返回 `{items,total}`,每项带
  本月 token/事件数/成本与未计价事件数);
- `GET /v1/admin/accounts/{account_id}`:单账号详情(`{account, usage}`,usage 与
  `/v1/usage/summary` 同口径);
- `PATCH /v1/admin/accounts/{account_id}/quota`:改配额(字段省略=保持、显式
  `null`=清除、整数 `>=1`;至少一个字段);
- `PATCH /v1/admin/accounts/{account_id}/role`:`user`/`admin`;改自己 409
  `admin.self_role`;
- `PATCH /v1/admin/accounts/{account_id}/status`:`active`/`disabled`;停用同一
  事务把该账号**全部** refresh token(`revoked_at`)与 sessions(`status=revoked`)
  吊销,并立即禁止其访问管理面;停用自己 409 `admin.self_disable`;
- `GET /v1/admin/audit-logs`:审计流水(可按 `actor_account_id`/`action`/
  `target_id` 过滤,`created_at desc, id desc`);
- `GET /v1/admin/cost/summary?days=30`:平台成本汇总(UTC 自然日窗口,含今天;
  `days` 1..90;按天补零、模型前 20、账号前 10,`platform`/`byok` 来源拆分与
  未计价条数;读端点不写审计)。

无权限访问管理端点返回 403 `admin.required`,并**先落一条 denied 审计**
(`admin.access.denied`,独立短事务,失败只告警)。写动作的审计与业务同事务:
`action` 为 `account.quota.update`/`account.role.update`/`account.status.update`,
`detail` 记 before/after(**不含任何密钥/令牌**),读端点不审计。`audit_logs`
append-only:服务只追加与分页读取,不提供改/删。

## API Key

- `GET /v1/api-keys`:列出当前账号的 key(只回前缀/掩码,不回明文);
- `POST /v1/api-keys`:新建,明文只在创建响应出现一次;
- `POST /v1/api-keys/{id}/rotate`:轮换(旧 key 立即失效);
- `DELETE /v1/api-keys/{id}`:删除;
- `POST /v1/api-keys/verify`:**内部**(`X-Internal-Secret`)校验 key 的
  sha256,响应 `ApiKeyVerifyRead{account_id,api_key_id,scopes}`——调用方据
  `account_id` 决定以哪个账号身份继续。

## 用量与配额

- `GET /v1/usage/summary`:当前账号近 30 天 token 用量汇总;
- `POST /v1/usage/events`:**内部**(共享密钥 + `X-Account-Id`)记一条用量;
- `GET /v1/usage/quota`:**内部**返回
  `UsageQuota{monthly_tokens,max_concurrent_runs,used_tokens}`,读
  `users.quota` JSON;字段缺失/非正数即**不限**(fail-open),新账号
  `quota={}` 默认不限。套餐(plan)概念已删除。

## BYOK(信封加密 + KMS 抽象)

用户自带的模型 API key 以**信封 v2** 落库:密文是 JSON
`{"v":2,"kms":"local","kid":"local-1","ct":"…"}`,`kid` 指向加密它的 KEK。
KMS 有 `local`(Fernet + 本地密钥材料)与 `vault`(Vault Transit)两种实现,
见 `account/core/byok_kms.py`;测试/特殊部署可经 `create_app(byok_kms=…)` 注入。

- `PUT /v1/byok/{provider}`:按当前 KMS 加密保存(响应只回 masked key);body 中
  `api_key` 可省略(`null`/空串 = **保留原密钥**,只改 `models`;首次保存缺 key 则
  400 `byok.invalid_key`),`models` 是该 provider 的自定义模型 id 清单(如
  `["openai/gpt-4o-mini"]`,`**整表替换**`,省略 = 清空;须 `<provider>/` 前缀、≤128
  字符、≤32 条,否则 400 `byok.invalid_model`)。`provider` 接受任意合法名
  (`^[a-z0-9][a-z0-9._-]{0,31}$`),不再限于内置几家;
- `GET /v1/byok` / `DELETE /v1/byok/{provider}`:列表(不回明文,含 `models`)与删除;
- `GET /v1/byok/catalog`:**内部**返回该账号全部 BYOK 的 `models` 并集
  (`{"models":[...]}`,按 provider 排序展开、去重保序,`Cache-Control: no-store`),
  供 llm-gateway 合并模型目录与放行白名单;
- `GET /v1/byok/{provider}/secret`:**内部解析明文**(`X-Internal-Secret` +
  `X-Account-Id`),供 llm-gateway 取用;响应带 `Cache-Control: no-store`。
  历史密文在读取时**懒轮换**到当前 kid,轮换失败只记 warning,仍返回明文;
- `POST /v1/byok/rotate`:**内部**把该账号全部密文重加密到当前 KMS key,
  逐条容错,返回 `{rotated,failed}`(失败记 `byok.rotate_failed`,不抛错)。

升级/轮换步骤(零停机):

1. 把当前 `BYOK_SECRET` 挪进 `BYOK_PREVIOUS_SECRETS`(JSON,key = 旧 kid);
2. 设新的 `BYOK_KEK_ID` + `BYOK_SECRET`,重启——旧密文仍可解;
3. 逐账号调 `POST /v1/byok/rotate` 把存量密文重加密到新 kid;
4. 确认 `failed=0` 后删掉 `BYOK_PREVIOUS_SECRETS`。

v2 之前的**裸 Fernet token** 保持可解(依次尝试当前/历史密钥),在读取或更新时
自动升级为 v2。从 `local` 迁到 `vault` 前必须先用 `local` 跑完一次轮换
(Vault 无法解裸 token)。解密失败统一 `500 byok.unreadable`,KMS 不可用统一
`502 byok.kms_unavailable`——都不泄漏密文/密钥内容。

| 变量                         | 默认     | 说明                                        |
| ---------------------------- | -------- | ------------------------------------------- |
| `BYOK_KMS`                   | local    | `local` 或 `vault`(未知取值启动即失败)      |
| `BYOK_KEK_ID`                | local-1  | 当前 KEK 标识,写进信封 `kid`                |
| `BYOK_SECRET`                | dev 占位 | `local` 当前 kid 的密钥材料                 |
| `BYOK_PREVIOUS_SECRETS`      | {}       | 历史 kid → 旧密钥材料的 JSON 映射(轮换期用) |
| `BYOK_VAULT_URL`             | 空       | Vault 地址(如 `http://vault:8200`)          |
| `BYOK_VAULT_TOKEN`           | 空       | Vault token(`vault` 模式必填)               |
| `BYOK_VAULT_MOUNT`           | transit  | Transit secrets engine 挂载点               |
| `BYOK_VAULT_KEY`             | byok     | Transit key 名                              |
| `BYOK_VAULT_TIMEOUT_SECONDS` | 5        | Vault HTTP 超时                             |

## 迁移

- `users`:一账号一行,`id` 即 `account_id`;唯一约束在 `subject`,
  `password_hash` 空 = 未设密码,`quota` JSON 默认 `{}`,`role` 默认 `user`
  (迁移时存量行自动回填);
- `audit_logs`:管理面审计流水(append-only,按 `actor_account_id`/`action`/
  `target_id`/`created_at` 建索引);
- `refresh_tokens` / `sessions` / `api_keys` / `usage_events` / `byok_keys` /
  `oauth_*`:全部账号私有,带 `account_id` 列(索引/唯一约束按
  `NAMING_CONVENTION` 命名);`oauth_clients` 是全局表,不带账号列。

命令(仓库根):`npx nx run account-service:serve`(:8001)/ `npx nx test account-service`
迁移:`cd services/account-service && uv run alembic upgrade head`

## 社交 OAuth(M0-05)

API 网关调用本服务的两个端点,浏览器不直接拿 token:

- `GET /v1/auth/oauth/{provider}/authorize?redirect_uri=...` → `{authorization_url,state}`
- `POST /v1/auth/oauth/{provider}/exchange` `{code,state}` → 与登录相同的扁平双令牌响应
  (首次登录建账号,同一身份复用账号)

配置: `ACCOUNT__GOOGLE_CLIENT_ID/SECRET`、`ACCOUNT__GITHUB_CLIENT_ID/SECRET`、
`ACCOUNT__OAUTH_STATE_SECRET`(留空回退 `ACCOUNT__INTERNAL_SECRET`)。

OAuth 应用回调地址(本地):

- Google: `http://localhost:5173/api/auth/oauth/google/callback`
- GitHub: `http://localhost:5173/api/auth/oauth/github/callback`

生产改成 `https://app.<domain>/api/auth/oauth/<provider>/callback`。

## Vercel 部署（导出约定）

Vercel Python runtime 只自动识别根级 `app.py`/`main.py` 等入口；本服务的 `app` 在
`account/main.py`，因此由 `pyproject.toml` 的 `[tool.vercel] entrypoint` 显式声明：

- 入口：`account.main:app`（与 `vercel.json` 的 `functions` key `account/main.py` 对应）
- Python 版本：`.python-version` 固定 3.12
- 函数配置：`vercel.json` 设 `maxDuration`，并排除 `tests/`、本地 `*.db`、`__pycache__`
  等（Python 包体上限 500MB，Vercel 不做 tree-shaking）

已知阻塞：服务依赖同仓库 uv workspace 内的 `common` / `database` / `events`，而
Vercel 以 `services/account-service` 为部署根、不包含仓库其他目录；真实部署前需先把这三个包
发布到私有索引或构建期 vendor。本次只覆盖无状态短请求服务，`agent-orchestrator` /
`workspace-service` 含常驻后台循环，按设计文档外置。详见
[serverless 部署方案](../../docs/design/serverless-deployment.md)。
