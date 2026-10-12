# knowledge-service

知识库服务(架构 §9):文档 **ingest → 分块 → 嵌入 → 检索**,租户隔离。

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

阶段 A 的边界:向量以 JSON 存在 `document_chunks.embedding` 列,检索时在
Python 侧算余弦相似度(小语料够用);换 pgvector/专用向量库是阶段 B,
读端契约不变。

命令(仓库根):`npx nx run knowledge-service:serve`(:8006)/ `npx nx test knowledge-service`
迁移:`cd services/knowledge-service && uv run alembic upgrade head`
