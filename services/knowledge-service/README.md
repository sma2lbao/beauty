# knowledge-service

知识库服务(架构 §9):文档 **ingest → 分块 → 嵌入 → 检索**,账号隔离。

- `POST /v1/knowledge/documents`:登记文档(标题 + 正文),服务端分块并向量化,
  返回 chunk 数;`GET /v1/knowledge/documents` 列表、`GET …/{id}` 详情、
  `DELETE …/{id}` 删除(连带 chunk);
- `POST /v1/knowledge/search`:混合检索(`mode=hybrid|vector|keyword`),
  返回带分数的命中块(文档标题/序号/正文)与 `mode`/`elapsed_ms`;
- `/healthz` `/readyz`:存活/就绪探针。

嵌入提供方(架构 §5 唯一出口,配置 `EMBEDDING_PROVIDER`):

- `local`(默认):无依赖的**确定性词法近似**向量(分词哈希 + L2 归一化,
  1024 维,与默认网关模型 bge-m3 同维,见 `knowledge/core/vectors.py`)。
  它不是语义嵌入(近义词不会靠近),用途是让骨架在没有模型/密钥时也能跑通
  ingest→search 全链路;生产请用 `gateway`;
- `gateway`:经 `llm-gateway` 的 `/v1/embeddings`(litellm 路由,如 bge-m3),
  失败统一 502 `llm.embedding_error`。

`gateway` 模式下,服务调用网关时会带 `X-Account-Id`(当前账号)与
`X-Internal-Secret`(`KNOWLEDGE__INTERNAL_SECRET`),网关据此解析该账号的
BYOK 密钥;该值须与 account-service / llm-gateway 的 `INTERNAL_SECRET` 一致,
留空则只按平台默认密钥解析(`gateway` 提供方在生产必须配置)。

存储与检索:

- `document_chunks.embedding` 是 `Vector(1024)`(pgvector),配 HNSW 索引
  (`m=16, ef_construction=64, vector_cosine_ops`);关键词路走 `pg_trgm` GIN
  索引 + `ILIKE` 粗筛 + 进程内词法打分(中文二元组,不依赖 zhparser),
  两路候选经 RRF(`k=60`) 融合,默认 `hybrid`。迁移
  `b7d4c2a19f30_pgvector_embedding` 会 `CREATE EXTENSION vector`/`pg_trgm`,
  目标库需允许建扩展(生产可由 DBA 预装,或依赖 pgvector 的 trusted 标记)。
- 维度是代码常量而非常量配置:换 embedding 模型需新增迁移 + 重嵌入,旧向量
  在迁移中置 NULL(不参与检索),不会产生错误分数。
- SQLite 覆盖库没有 pgvector:同一列类型按文本存取,检索退回 Python 侧余弦
  (只用于测试/离线,规模上限与阶段 A 相同)。

命令(仓库根):`npx nx run knowledge-service:serve`(:8006)/ `npx nx test knowledge-service`
迁移:`cd services/knowledge-service && uv run alembic upgrade head`

## 部署

本服务随仓库在腾讯云轻量服务器（首尔）上用 Docker Compose 部署；Vercel 只承载
`apps/web` / `apps/site` 静态层。编排见
[deploy/tencent/docker-compose.cvm.yml](../../deploy/tencent/docker-compose.cvm.yml)，
方案与验收见 [hybrid 部署方案](../../docs/design/hybrid-deployment.md) 与
[deploy/tencent/README.md](../../deploy/tencent/README.md)。

服务目录不再包含 Vercel 导出配置（`vercel.json` / `.python-version` /
`pyproject.toml` 的 `[tool.vercel]`），这些已于 2026-11-12 随部署主线切换清理。
