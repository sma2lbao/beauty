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
