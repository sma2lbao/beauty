# api-gateway

同源 API 门面:浏览器只访问 `/api`,由 Nginx 反代到本服务(API 网关),再由 API 网关访问内部 Python 服务。

M0-01 只提供健康检查骨架;M0-05 起补 httpOnly Cookie/OAuth 会话,M0-06 起补代理、错误契约、限流、下载与 stream token。

## 本地启动

```sh
npx nx run api-gateway:serve
curl http://127.0.0.1:8007/healthz
curl http://127.0.0.1:8007/api/health
```

环境变量前缀 `API_GATEWAY__`,见 `.env.example`。

## 会话与同源 API(M0-05/06)

- `/api/auth/*`:登录/注册/刷新/登出/session/OAuth start+callback;双令牌只进
  加密 httpOnly Cookie,响应体不含 token;写请求要求 `X-CSRF-Token`。
- `/api/v1/*` → orchestrator; `/api/account|workspace|knowledge/*` → 对应服务;
  下游 401 自动刷新一次;SSE 流式转发;限流默认 120/min。
- `GET /api/files/{artifactId}`:带会话的产物下载。
- `POST /api/stream-token`:`{run_id}` → 短时 token + runtime SSE URL。
- 开发兼容:没有 Cookie 时允许 `Authorization: Bearer` 直通(保留 token 模式联调)。

## Vercel 部署（导出约定）

Vercel Python runtime 只自动识别根级 `app.py`/`main.py` 等入口；本服务的 `app` 在
`api_gateway/main.py`，因此由 `pyproject.toml` 的 `[tool.vercel] entrypoint` 显式声明：

- 入口：`api_gateway.main:app`（与 `vercel.json` 的 `functions` key `api_gateway/main.py` 对应）
- Python 版本：`.python-version` 固定 3.12
- 依赖安装：`vercel.json` 的 `installCommand` 显式跑
  `uv sync --active --no-dev --no-editable --package api-gateway --frozen`，
  把 workspace 依赖（`common` 等）以普通包装进 builder 的 venv；**不要留空**——
  空字符串等于跳过 Install Command，函数运行时会 `ModuleNotFoundError: No module named 'httpx'`
- 依赖自足：函数只装本服务 `pyproject.toml` 里声明的依赖，本地共享 `.venv` 会被
  兄弟服务的 extra 掩盖。本服务的 `schemas/auth.py` 用 `EmailStr`，所以
  `pydantic[email]` 必须留在 dependencies 里，否则函数里报
  `email-validator is not installed`
- 函数配置：`vercel.json` 设 `maxDuration: 300`（300s 是 Hobby/非 Pro 计划上限，
  800s 需 Pro；详见设计文档），并排除 `tests/`、本地 `*.db`、`__pycache__`
  等（Python 包体上限 500MB，Vercel 不做 tree-shaking）

自定义 Install Command 会让 Vercel 放弃函数包体优化、把整份 site-packages 打进 `_vendor`
（实测约 40MB，远低于 500MB 上限），换来的 `--no-editable` 安装让 `common` / `database` /
`events` 不再依赖指向 `../../packages/*` 的 editable `.pth`，因此无需私有索引或额外
vendor 步骤即可在函数里导入。本次只覆盖无状态短请求服务，`agent-orchestrator` /
`workspace-service` 含常驻后台循环，按设计文档外置。详见
[serverless 部署方案](../../docs/design/serverless-deployment.md)。
