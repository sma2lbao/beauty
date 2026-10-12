# common

beauty-moon 服务基础设施共享库。架构定位见 `docs/architecture.md`。

当前(骨架阶段)内容:

- `common.errors`:统一业务错误模型(错误码是对外 API 契约的一部分)

随实现逐步补齐:配置基类(pydantic-settings)、结构化日志、FastAPI 公共依赖(认证/DB session)、OTel 装配、SSE 工具。

命令(仓库根执行):`npx nx lint common` / `npx nx format common` / `npx nx test common` / `npx nx sync common`
