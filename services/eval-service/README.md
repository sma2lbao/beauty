# eval-service

评测平台服务（端口 **8009**，数据库 `beauty_eval`）。

- `POST /v1/runs`：CI/本地通过 `X-Internal-Secret` 上报 evalkit JSON 报告；
  同 `external_id` 幂等，重复上报返回已有运行。
- `GET /v1/runs`：运行索引，支持 `suite/status/branch` 过滤；鉴权开启时校验
  账号 JWT，`EVAL__AUTH_ENABLED=false` 时匿名只读。
- `GET /v1/runs/{id}`：完整报告；服务会把基线漂移回填到 `drifts`/
  `suite.drift_count`/`case.baseline_diff`。
- `GET /v1/runs/{id}/suites/{suite}/cases`：用例服务端分页（`status` 过滤）。
- `GET /v1/baselines`、`POST /v1/baselines/{suite}`：已批准基线的读取与替换。

## 本地运行

```sh
npx nx run eval-service:sync
npx nx run eval-service:serve
```

上报最近一次报告：

```sh
node scripts/eval/upload.mjs
```

认证：`EVAL__AUTH_ENABLED=true` 时读接口校验账号 JWT；关闭时只读接口匿名
可用（本地/演示，网关仍做会话鉴权），基线批准仍要求账号。写接口只认
`EVAL__INTERNAL_SECRET`，未配置时返回 503（fail closed）。

## 迁移

```sh
cd services/eval-service && uv run alembic upgrade head
```
