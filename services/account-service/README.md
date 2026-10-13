# account-service

账号服务(架构 §3):**用户 / 组织 / 配额 / 套餐 + sessions/devices**。
认证本身交给 IdP(Logto)——本服务只做业务侧的组织与成员管理(§10)。

- `GET /v1/organization` / `PATCH /v1/organization`:当前租户的组织档案与配额
  (计划 plan + `quota` JSON);首次读取**幂等建档**,便于 dev/test 直接调用;
- `POST /v1/users`、`GET /v1/users`、`GET /v1/users/{id}`、
  `PATCH /v1/users/{id}`:成员管理(`subject` = IdP sub,同租户唯一;
  角色 owner/member;状态 active/disabled);
- `POST /v1/sessions`、`GET /v1/sessions`、`POST /v1/sessions/{id}/revoke`:
  多端会话/设备登记与吊销(app 预留,§7);
- `/healthz` `/readyz`:存活/就绪探针。

## dev IdP 桩(阶段 A,`DEV_AUTH_ENABLED=true` 时挂载)

在 Logto 接入前,本服务兼作**登录替身**,让"登录 → 签发 access token →
各服务验签"的完整生产路径先跑起来(§10/§11):

- `POST /v1/auth/login`:声明 email 即登录(无密码——桩验证的是**认证管道**,
  凭证校验归 IdP)。幂等:组织按 `organization_id` 建档、成员按 (租户, email)
  复用(首个成员 owner)、每次登录登记一条 session;返回 RS256 access token
  (claims: `iss`/`aud`/`sub`/`tenant_id`/`exp`,与 IdP 契约同构);
- `POST /v1/auth/issue`:**内部服务签发**(服务间调用用,`X-Internal-Secret`
  匹配 `DEV_INTERNAL_SECRET` 才放行,密钥未配置即端点关闭):按目标租户换
  标准 token——orchestrator 的下游调用经它与用户面走**同一个** JwtVerifier,
  不设后门;
- `GET /.well-known/openid-configuration` + `GET /jwks`:OIDC 发现文档与
  JWKS,兄弟服务的 JwtVerifier 全自动发现验签(进程启动时生成 RSA 密钥,
  重启即轮换 kid);
- 换 Logto 时:**各服务只改 `AUTH_ISSUER`**,代码零改动(桩不注入任何
  专有后门)。

组织表**不带 tenant_id**:组织的 id 本身就是租户标识(与各处 `X-Tenant-Id` /
JWT claim 对齐);其余表都是租户隔离的。

命令(仓库根):`npx nx run account-service:serve`(:8001)/ `npx nx test account-service`
迁移:`cd services/account-service && uv run alembic upgrade head`
