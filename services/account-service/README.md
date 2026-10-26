# account-service

账号服务(架构 §3):**用户 / 组织 / 配额 / 套餐 + sessions/devices**,
以及**自建认证**(注册/登录/刷新,由阶段 A 的 dev IdP 桩转正,§10/§11)。

- `GET /v1/organization` / `PATCH /v1/organization`:当前租户的组织档案与配额
  (计划 plan + `quota` JSON);首次读取**幂等建档**,便于 dev/test 直接调用;
- `POST /v1/users`、`GET /v1/users`、`GET /v1/users/{id}`、
  `PATCH /v1/users/{id}`:成员管理(`subject` = 签发方 sub,同租户唯一;
  角色 owner/member;状态 active/disabled);
- `POST /v1/sessions`、`GET /v1/sessions`、`POST /v1/sessions/{id}/revoke`:
  多端会话/设备登记与吊销(app 预留,§7);
- `/healthz` `/readyz`:存活/就绪探针。

## 自建认证(始终挂载)

本服务兼作 IdP:凭证校验(密码)+ 令牌签发(RS256)+ OIDC 发现/JWKS,
兄弟服务用同一个 `JwtVerifier` 经 HTTP 发现自动验签——**不设任何后门**,
将来要换 Logto 等托管 IdP 只改各服务 `AUTH_ISSUER`,代码零改动。

- `POST /v1/auth/register`:注册即建组织(新租户)+ owner 成员;
  email 全局唯一(409 `account.email_taken`);密码最小 8 位,argon2id 哈希;
- `POST /v1/auth/login`:邮箱 + 密码换**双令牌**;失败统一 401,不区分
  "用户不存在"与"密码错"(防枚举;用户不存在时也跑一次假哈希校验);
- `POST /v1/auth/refresh`:**旋转式刷新**——旧 refresh token 一次性,用后即
  作废并签新对;已作废令牌被再次使用视为**泄露信号**,吊销该用户全部
  刷新令牌;库中只存 sha256,令牌本体 = 256 位随机 urlsafe;
- `POST /v1/auth/logout`:吊销指定 refresh token(幂等);access 不设黑名单,
  自然过期(默认 1 小时);
- `POST /v1/auth/issue`:**内部服务签发**(服务间调用用,`X-Internal-Secret`
  匹配 `INTERNAL_SECRET` 才放行,密钥未配置即端点关闭):按目标租户换
  标准 token——orchestrator 的下游调用经它与用户面走**同一个** JwtVerifier;
- `GET /.well-known/openid-configuration` + `GET /jwks`:OIDC 发现文档与 JWKS。

### 配置(前缀 `ACCOUNT__`)

| 变量                  | 默认       | 说明                                                                                   |
| --------------------- | ---------- | -------------------------------------------------------------------------------------- |
| `TOKEN_TTL_SECONDS`   | 3600       | access token 有效期                                                                    |
| `REFRESH_TTL_SECONDS` | 2592000    | refresh token 有效期(30 天)                                                            |
| `TOKEN_AUDIENCE`      | beauty-api | JWT `aud`(与各服务 `AUTH_AUDIENCE` 一致)                                               |
| `SIGNING_KEY_PATH`    | 空         | 签名私钥 PEM 路径:设置则跨重启不轮换 kid(生产必设);留空则进程内生成(dev/test)          |
| `INTERNAL_SECRET`     | 空         | `/v1/auth/issue` 的共享密钥,留空即关闭内部签发                                         |
| `AUTH_ENABLED`        | true       | 校验 Bearer 并从 token claim 解析租户(自建 IdP 即本服务;显式关闭走旧临时头,仅特殊部署) |

## BYOK(信封加密 + KMS 抽象)

用户自带的模型 API key 以**信封 v2** 落库:密文是 JSON
`{"v":2,"kms":"local","kid":"local-1","ct":"…"}`,`kid` 指向加密它的 KEK。
KMS 有 `local`(Fernet + 本地密钥材料)与 `vault`(Vault Transit)两种实现,
见 `account/core/byok_kms.py`;测试/特殊部署可经 `create_app(byok_kms=…)` 注入。

- `PUT /v1/byok/{provider}`:按当前 KMS 加密保存(响应只回 masked key);
- `GET /v1/byok` / `DELETE /v1/byok/{provider}`:列表(不回明文)与删除;
- `GET /v1/byok/{provider}/secret`:**内部解析明文**(`X-Internal-Secret` +
  `X-Tenant-Id`),供 llm-gateway 取用;响应带 `Cache-Control: no-store`。
  历史密文在读取时**懒轮换**到当前 kid,轮换失败只记 warning,仍返回明文;
- `POST /v1/byok/rotate`:**内部**把租户全部密文重加密到当前 KMS key,
  逐条容错,返回 `{rotated,failed}`(失败记 `byok.rotate_failed`,不抛错)。

升级/轮换步骤(零停机):

1. 把当前 `BYOK_SECRET` 挪进 `BYOK_PREVIOUS_SECRETS`(JSON,key = 旧 kid);
2. 设新的 `BYOK_KEK_ID` + `BYOK_SECRET`,重启——旧密文仍可解;
3. 逐租户调 `POST /v1/byok/rotate` 把存量密文重加密到新 kid;
4. 确认 `failed=0` 后删掉 `BYOK_PREVIOUS_SECRETS`。

v2 之前的**裸 Fernet token** 保持可解(依次尝试当前/历史密钥),在读取或更新时
自动升级为 v2。从 `local` 迁到 `vault` 前必须先用 `local` 跑完一次轮换
(Vault 无法解裸 token)。解密失败统一 `500 byok.unreadable`,KMS 不可用统一
`502 byok.kms_unavailable`——都不泄漏密文/密钥内容。

| 变量                         | 默认    | 说明                                          |
| ---------------------------- | ------- | --------------------------------------------- |
| `BYOK_KMS`                   | local   | `local` 或 `vault`(未知取值启动即失败)        |
| `BYOK_KEK_ID`                | local-1 | 当前 KEK 标识,写进信封 `kid`                   |
| `BYOK_SECRET`                | dev 占位 | `local` 当前 kid 的密钥材料                  |
| `BYOK_PREVIOUS_SECRETS`      | {}      | 历史 kid → 旧密钥材料的 JSON 映射(轮换期用)   |
| `BYOK_VAULT_URL`             | 空      | Vault 地址(如 `http://vault:8200`)            |
| `BYOK_VAULT_TOKEN`           | 空      | Vault token(`vault` 模式必填)                 |
| `BYOK_VAULT_MOUNT`           | transit | Transit secrets engine 挂载点                 |
| `BYOK_VAULT_KEY`             | byok    | Transit key 名                                |
| `BYOK_VAULT_TIMEOUT_SECONDS` | 5       | Vault HTTP 超时                               |

## 迁移

users 表 `password_hash`(空 = 未设密码,存量/被邀请用户);
refresh_tokens 表(哈希 + 过期/吊销时间,租户隔离)。

组织表**不带 tenant_id**:组织的 id 本身就是租户标识(与各处 JWT claim 对齐);
其余表都是租户隔离的。

命令(仓库根):`npx nx run account-service:serve`(:8001)/ `npx nx test account-service`
迁移:`cd services/account-service && uv run alembic upgrade head`

## 社交 OAuth(M0-05)

BFF 调用本服务的两个端点,浏览器不直接拿 token:

- `GET /v1/auth/oauth/{provider}/authorize?redirect_uri=...` → `{authorization_url,state}`
- `POST /v1/auth/oauth/{provider}/exchange` `{code,state}` → 与登录相同的双令牌响应

配置: `ACCOUNT__GOOGLE_CLIENT_ID/SECRET`、`ACCOUNT__GITHUB_CLIENT_ID/SECRET`、
`ACCOUNT__OAUTH_STATE_SECRET`(留空回退 `ACCOUNT__INTERNAL_SECRET`)。

OAuth 应用回调地址(本地):

- Google: `http://localhost:5173/api/auth/oauth/google/callback`
- GitHub: `http://localhost:5173/api/auth/oauth/github/callback`

生产改成 `https://app.<domain>/api/auth/oauth/<provider>/callback`。
