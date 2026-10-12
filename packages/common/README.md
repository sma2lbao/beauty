# common

beauty-moon 服务基础设施共享库。架构定位见 `docs/architecture.md`。

当前内容:

- `common.errors`:统一业务错误模型(错误码是对外 API 契约的一部分)——
  `NotFoundError`(404)/`PermissionDeniedError`(403)/`ConflictError`(409)、
  `UpstreamError`(502,外部依赖失败)、`UnauthorizedError`(401,凭证不可信);
- `common.config`:`BaseAppSettings` 基类(env_file、service_name/env/log_level);
- `common.logging`:structlog 装配(dev 彩色、staging/prod JSON)与 `get_logger`;
- `common.auth`:`JwtVerifier`——IdP(Logto)access token 校验与租户提取:
  OIDC 发现 + JWKS 拉取(TTL 缓存、未知 kid 触发刷新以支持密钥轮换)、
  RS256 签名 + `iss`/`aud`/`exp` 校验;租户来自可配置 claim,缺失即拒绝
  (绝不回退请求头);JWKS 不可达是 502(上游问题);fetcher 可注入,测试零网络。

随实现逐步补齐:FastAPI 公共依赖(分页/DB session 复用)、OTel 装配、SSE 工具。

命令(仓库根执行):`npx nx lint common` / `npx nx format common` / `npx nx test common` / `npx nx run common:sync`
