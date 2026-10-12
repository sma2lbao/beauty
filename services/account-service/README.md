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

组织表**不带 tenant_id**:组织的 id 本身就是租户标识(与各处 `X-Tenant-Id` /
JWT claim 对齐);其余表都是租户隔离的。

命令(仓库根):`npx nx run account-service:serve`(:8001)/ `npx nx test account-service`
迁移:`cd services/account-service && uv run alembic upgrade head`
