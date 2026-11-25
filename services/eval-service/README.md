# eval-service

评测平台服务（端口 **8009**，数据库 `beauty_eval`）。

- `POST /v1/runs`：CI/本地通过 `X-Internal-Secret` 上报 evalkit JSON 报告；
  同 `external_id` 幂等，重复上报返回已有运行。
- `GET /v1/runs`：运行索引，支持 `suite/status/branch` 过滤；鉴权开启时校验
  账号 JWT，`EVAL__AUTH_ENABLED=false` 时匿名只读。
- `GET /v1/runs/{id}`：完整报告；服务会把基线漂移回填到 `drifts`/
  `suite.drift_count`/`case.baseline_diff`。报告超过 64KB 时完整 JSON 转存
  对象存储，DB 只留 `report_key` 与摘要；对象丢失返回 410，存储未装配返回 503。
- `GET /v1/runs/{id}/summary`：运行摘要（元数据 + 套件汇总，不含逐用例）。
- `GET /v1/runs/{id}/suites/{suite}/cases`：用例服务端分页（`status` 过滤）。
- `GET /v1/runs/{id}/case?suite=&case_id=`：单用例详情（`case_id` 含 `/`，走 query）。
- `GET /v1/runs/{id}/download`：下载完整报告附件；成功/失败都写
  `eval_report_downloads`（actor、IP、UA、request_id、report_key、状态与错误码）。
- `GET /v1/downloads`：下载审计清单（仅内部密钥），支持 `run_id` 过滤与分页。
- `GET /v1/compare?base=&candidate=`：两次运行的用例级对比，标记
  regressed/fixed/added/removed/unchanged 并给出通过率、tokens、耗时差值。
- `GET /v1/datasets`、`GET /v1/datasets/{name}`：从 `evals/datasets/` 只读扫描数据集，
  用 evalkit schema 校验；坏数据集保留在清单中并标 `valid=false`。
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

## 存储与保留期

`EVAL__ARTIFACT_STORE` 取 `database`（默认，不拆分）、`filesystem`
（`EVAL__ARTIFACT_ROOT`，compose 挂 `eval-artifacts` 卷）或 `cos`。
`EVAL__REPORT_INLINE_MAX_BYTES` 控制转存阈值（默认 65536）。

按保留期清理过期运行、用例/套件行与报告对象：

```sh
npx nx run eval-service:retention -- --days 90 --dry-run
npx nx run eval-service:retention -- --days 90
npx nx run eval-service:maintenance -- loop --once     # 常驻前的单轮
npx nx run eval-service:maintenance -- loop            # 常驻，默认每 86400s
```

`EVAL__RETENTION_DAYS` 默认 90，设为 0 时 maintenance 命令直接跳过；
`EVAL__RETENTION_INTERVAL_SECONDS` 控制常驻间隔。compose 的 `eval-retention`
用同一镜像跑 `loop`，无 HTTP 端口。

COS 生命周期（默认 dry-run，确认后加 `--apply`，要求
`EVAL__ARTIFACT_STORE=cos`）：

```sh
npx nx run eval-service:maintenance -- lifecycle --days 180
npx nx run eval-service:maintenance -- lifecycle --days 180 --apply
```

默认前缀 `eval-runs/`（`EVAL__ARTIFACT_LIFECYCLE_PREFIX`），180 天过期并清理
未完成分片；filesystem 后端由保留期清理负责。

## 迁移

```sh
cd services/eval-service && uv run alembic upgrade head
```
