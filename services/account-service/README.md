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

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TOKEN_TTL_SECONDS` | 3600 | access token 有效期 |
| `REFRESH_TTL_SECONDS` | 2592000 | refresh token 有效期(30 天) |
| `TOKEN_AUDIENCE` | beauty-api | JWT `aud`(与各服务 `AUTH_AUDIENCE` 一致) |
| `SIGNING_KEY_PATH` | 空 | 签名私钥 PEM 路径:设置则跨重启不轮换 kid(生产必设);留空则进程内生成(dev/test) |
| `INTERNAL_SECRET` | 空 | `/v1/auth/issue` 的共享密钥,留空即关闭内部签发 |

## 迁移

users 表 `password_hash`(空 = 未设密码,存量/被邀请用户);
refresh_tokens 表(哈希 + 过期/吊销时间,租户隔离)。

组织表**不带 tenant_id**:组织的 id 本身就是租户标识(与各处 JWT claim 对齐);
其余表都是租户隔离的。

命令(仓库根):`npx nx run account-service:serve`(:8001)/ `npx nx test account-service`
迁移:`cd services/account-service && uv run alembic upgrade head`
