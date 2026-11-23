# knowledge-service

知识库服务(架构 §9):文档 **ingest → 分块 → 嵌入 → 检索**,账号隔离。

- `POST /v1/knowledge/documents`:登记文档(标题 + 正文),服务端分块并向量化,
  返回 chunk 数;`GET /v1/knowledge/documents` 列表、`GET …/{id}` 详情、
  `DELETE …/{id}` 删除(连带 chunk);
- `POST /v1/knowledge/search`:混合检索(`mode=hybrid|vector|keyword`),
  返回带分数的命中块(文档标题/序号/正文)与 `mode`/`elapsed_ms`;开了重排时
  额外回 `rerank`(`applied`/`skipped`)与 `rerank_model`,此时 `score` 是重排相关性分数;
- `/healthz` `/readyz`:存活/就绪探针。

嵌入提供方(架构 §5 唯一出口,配置 `EMBEDDING_PROVIDER`):

- `local`(默认):无依赖的**确定性词法近似**向量(分词哈希 + L2 归一化,
  1024 维,与默认网关模型 bge-m3 同维,见 `knowledge/core/vectors.py`)。
  它不是语义嵌入(近义词不会靠近),用途是让骨架在没有模型/密钥时也能跑通
  ingest→search 全链路;生产请用 `gateway`;
- `gateway`:经 `llm-gateway` 的 `/v1/embeddings`(litellm 路由,如 bge-m3),
  失败统一 502 `llm.embedding_error`。服务**一次提交全部块**,网关按提供商行数
  上限切批(默认 10 行/批,百炼 text-embedding-v3/v4 的上限)。接百炼时把
  `EMBEDDING_MODEL` 设为 `dashscope/text-embedding-v4`(默认 1024 维,与向量列
  一致),`gateway` 侧用 `LLM_GATEWAY__EMBEDDING_API_KEY/API_BASE` 配百炼密钥与
  `https://dashscope.aliyuncs.com/compatible-mode/v1`(对话与嵌入不同家时必须分开配)。

重排提供方(可选,配置 `RERANK_PROVIDER`,默认关闭):

- `none`(默认):不重排,直接用 RRF 粗排顺序,响应里 `rerank=null`;
- `gateway`:混合检索先取 `max(limit, RERANK_CANDIDATES,默认 20)` 条候选,
  经 `llm-gateway` 的 `/v1/rerank`(litellm 路由,默认
  `dashscope/qwen3-rerank`,百炼单请求 500 篇 / 4k token 每篇)按查询重打分,
  再按新分数截到 `limit`;此时 `score` 是 0~1 的重排相关性分数。
  **降级是设计的一部分**:网关不可达/超时/返回条数不符时记 warn、退回原顺序并
  回 `rerank="skipped"`(HTTP 仍 200);候选只有 1 条时也标 `skipped`(不值得多打一次)。
  凭据与嵌入同源:网关侧 `RERANK_API_KEY/API_BASE` 留空会回退嵌入专用配置。

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
