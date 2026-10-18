# bff

同源 API 门面:浏览器只访问 `/api`,由 Nginx 反代到本服务(BFF),再由 BFF 访问内部 Python 服务。

M0-01 只提供健康检查骨架;M0-05 起补 httpOnly Cookie/OAuth 会话,M0-06 起补代理、错误契约、限流、下载与 stream token。

## 本地启动

```sh
npx nx run bff:serve
curl http://127.0.0.1:8007/healthz
curl http://127.0.0.1:8007/api/health
```

环境变量前缀 `BFF__`,见 `.env.example`。

## 会话与同源 API(M0-05/06)

- `/api/auth/*`:登录/注册/刷新/登出/session/OAuth start+callback;双令牌只进
  加密 httpOnly Cookie,响应体不含 token;写请求要求 `X-CSRF-Token`。
- `/api/v1/*` → orchestrator; `/api/account|workspace|knowledge/*` → 对应服务;
  下游 401 自动刷新一次;SSE 流式转发;限流默认 120/min。
- `GET /api/files/{artifactId}`:带会话的产物下载。
- `POST /api/stream-token`:`{run_id}` → 短时 token + runtime SSE URL。
- 开发兼容:没有 Cookie 时允许 `Authorization: Bearer` 直通(保留 token 模式联调)。
