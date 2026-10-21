# connector-service

连接器服务**框架**(架构 §8):租户隔离的外部账号连接存储、凭证加密与撤销。
当前**没有内置连接器**——邮件(IMAP/SMTP、Gmail/Outlook 收发信与附件)与日历
(Google Calendar / Microsoft 365 日程)能力均已下线;服务继续保留,作为后续
接入新连接器的基座。

## 端点

- `GET /v1/connections`:同租户连接列表(**凭证只进不出**——任何读端点都不
  返回凭证字段);
- `GET /v1/connections/{id}`:连接详情(跨租户一律 404,不泄漏存在性);
- `DELETE /v1/connections/{id}`:撤销(留痕不物理删;状态守卫:仅 active 可撤,
  重复撤销 409 `connector.invalid_state`);
- `/healthz` `/readyz`:存活/就绪探针(数据库可用即就绪)。

服务当前不提供创建连接的端点:连接行来自历史数据或未来接入的 provider。

## 安全与凭证

凭证经应用层信封加密(`connector/core/crypto.py`,Fernet)存 JSON 列,读端绝不
外泄;密文密钥走 `CONNECTOR__CREDENTIAL_SECRET`(生产必须换随机长密钥/KMS)。
鉴权与 agent-orchestrator 同一套约定(默认开启):租户来自 IdP token claim
(伪造 `X-Tenant-Id` 无效),显式关闭鉴权才用临时头(仅特殊部署)。

## 接入新连接器

1. 在 `connector/models/connections.py` 复用连接表(provider/account 唯一约束、
   status 状态机、credentials 加密列);
2. 新增 `connector/services/<provider>.py` 封装外部 API 调用(凭证经
   `encrypt_credentials`/`decrypt_credentials` 存取,不要在日志/响应中输出);
3. 新增 `connector/api/v1/<provider>.py` 路由并用 `include_router` 挂到
   `connector/api/v1/router.py`;读端点统一返回 `ConnectionRead`(无凭证字段);
4. 如需 OAuth/授权码流程或后台同步,按 provider 另行实现(当前框架未内置);
5. 新增配置项写到 `connector/core/config.py` 并同步 `.env.example`。

## 迁移

```sh
cd services/connector-service && uv run alembic upgrade head
```

迁移 `b2e4c7a91d33` 把存量**所有非 revoked 连接**置为 `revoked`(邮件与日历
能力均下线,保留行留痕);该数据清理不可逆,downgrade 为 no-op。`connections.provider`
列注释同步为「历史数据;无内置 provider」。

## 命令(仓库根)

- `npx nx run connector-service:serve`(:8004)
- `npx nx test connector-service`
