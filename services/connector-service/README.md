# connector-service

连接器服务(阶段 A 骨架):外部系统的**账号连接与凭证管理**(架构 §8),
邮件优先——`MailTransport` 协议统一所有提供商,API 层零感知:

- `POST /v1/connections` 注册连接(阶段 A 仅 `imap`:QQ/163 等授权码,
  授权码即密码);**凭证只进不出**——任何读端点都不返回凭证字段;
- `GET /v1/connections`、`GET /v1/connections/{id}`:同租户连接列表/详情;
- `DELETE /v1/connections/{id}`:撤销(留痕不物理删,状态守卫:仅 active 可撤);
- `POST /v1/connections/{id}/test`:连通性测试(IMAP 登录探活),更新 last_checked_at;
- `GET /v1/connections/{id}/emails?limit=10`:收件箱最近 N 封(只读);
- `POST /v1/connections/{id}/emails`:发信(写操作,SMTP_SSL;`smtp_host` 缺省由
  `imap.<域>` 推导、端口 465);调用方(agent-orchestrator 的确认闸)必须先拿到
  人工放行,本服务不做二次判断;失败统一 502 `connector.send_failed`;
- `/healthz` `/readyz`:存活/就绪探针(数据库可用即就绪)。

安全与凭证:阶段 A 凭证明文存 JSON 列(读端绝不外泄,测试覆盖);
密文存储(信封加密 + 密钥库)与 OAuth 双轨(Gmail/M365)为阶段 B(§10)。
鉴权与 agent-orchestrator 同一套约定(默认开启):租户来自 IdP
token claim(伪造 `X-Tenant-Id` 无效),显式关闭鉴权才用临时头(仅特殊部署)。
连接生命周期事件(created/revoked)在事件总线/outbox 落地时接入。

命令(仓库根):`npx nx run connector-service:serve`(:8004)/ `npx nx test connector-service`
迁移:`cd services/connector-service && uv run alembic upgrade head`
