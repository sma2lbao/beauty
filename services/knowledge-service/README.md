# knowledge-service

知识库服务(架构 §9):文档 **ingest → 分块 → 嵌入 → 检索**,账号隔离。

- `POST /v1/knowledge/documents`:登记文档(标题 + 正文),服务端分块并向量化,
  返回 chunk 数;`GET /v1/knowledge/documents` 列表、`GET …/{id}` 详情、
  `DELETE …/{id}` 删除(连带 chunk);
- `POST /v1/knowledge/search`:向量检索,返回带分数的命中块(文档标题/序号/正文);
- `/healthz` `/readyz`:存活/就绪探针。

嵌入提供方(架构 §5 唯一出口,配置 `EMBEDDING_PROVIDER`):

- `local`(默认):无依赖的**确定性词法近似**向量(分词哈希 + L2 归一化)。
  它不是语义嵌入(近义词不会靠近),用途是让骨架在没有模型/密钥时也能跑通
  ingest→search 全链路;生产请用 `gateway`;
- `gateway`:经 `llm-gateway` 的 `/v1/embeddings`(litellm 路由,如 bge-m3),
  失败统一 502 `llm.embedding_error`。

`gateway` 模式下,服务调用网关时会带 `X-Account-Id`(当前账号)与
`X-Internal-Secret`(`KNOWLEDGE__INTERNAL_SECRET`),网关据此解析该账号的
BYOK 密钥;该值须与 account-service / llm-gateway 的 `INTERNAL_SECRET` 一致,
留空则只按平台默认密钥解析(`gateway` 提供方在生产必须配置)。

阶段 A 的边界:向量以 JSON 存在 `document_chunks.embedding` 列,检索时在
Python 侧算余弦相似度(小语料够用);换 pgvector/专用向量库是阶段 B,
读端契约不变。

命令(仓库根):`npx nx run knowledge-service:serve`(:8006)/ `npx nx test knowledge-service`
迁移:`cd services/knowledge-service && uv run alembic upgrade head`

## Vercel 部署（导出约定）

Vercel Python runtime 只自动识别根级 `app.py`/`main.py` 等入口；本服务的 `app` 在
`knowledge/main.py`，因此由 `pyproject.toml` 的 `[tool.vercel] entrypoint` 显式声明：

- 入口：`knowledge.main:app`（与 `vercel.json` 的 `functions` key `knowledge/main.py` 对应）
- Python 版本：`.python-version` 固定 3.12
- 函数配置：`vercel.json` 设 `maxDuration`，并排除 `tests/`、本地 `*.db`、`__pycache__`
  等（Python 包体上限 500MB，Vercel 不做 tree-shaking）

已知阻塞：服务依赖同仓库 uv workspace 内的 `common` / `database` / `events`，而
Vercel 以 `services/knowledge-service` 为部署根、不包含仓库其他目录；真实部署前需先把这三个包
发布到私有索引或构建期 vendor。本次只覆盖无状态短请求服务，`agent-orchestrator` /
`workspace-service` 含常驻后台循环，按设计文档外置。详见
[serverless 部署方案](../../docs/design/serverless-deployment.md)。
