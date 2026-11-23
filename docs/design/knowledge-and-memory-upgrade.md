# 知识库与记忆模块：主流方案对标与改造清单

> 状态：评审稿（2026-11-22） · 关联：[memory.md](./memory.md)（记忆模块唯一真源）、[architecture.md](../architecture.md) §9（知识库）、[roadmap.md](../roadmap.md)
> 定位：本文只回答两件事——**主流怎么做的**、**本项目要改哪里**。落地后知识库部分迁出为 `design/knowledge.md`（目前知识库没有设计真源，这本身就是缺口之一），记忆部分回写 `memory.md` 的 D30+。
> 基线：工作区 2026-11-22 快照；文中 `file.py:line` 以符号名检索为准。

## 1. 结论

**记忆模块**已经不是「简约」档：短期压缩、长期记忆分层注入（core → convention → recall）、BM25 + 向量 + MMR、时效替代链、低置信度复核、敏感过滤、TTL 三态、导出/彻底删除、mem0/Zep 薄适配、golden 检索评测——这些在开源 harness 里属于**第一梯队**。真正落后的是三项**结构能力**：

1. **没有向量索引**：召回候选固定 `_CANDIDATE_LIMIT=200`（`services/agent-orchestrator/agent_orchestrator/services/memory/constants.py`），超过 200 条后靠 `list_active` 的排序截断，老而相关的记忆直接掉出候选集——这是**正确性**问题，不是性能问题。
2. **没有关系与时间图**：只有 `valid_from/invalid_at/superseded_by` 的单链替代，回答不了「谁在哪段时间负责什么」「这个结论来自哪次对话的哪句话」这类多跳问题。
3. **后台整理只有词面去重**：sweeper 的「巩固」是按 0.92 字符相似度软删（`services/memory_sweeper.py`），无法把「周报用表格」与「周报模板走三列表格」合并成一条更准的记忆。

**知识库**是真正的 MVP：2 张表、JSON 列存向量、Python 侧全量余弦、`chunk_text(400/60)` 定长滑窗、只有 `query + limit` 两个检索参数、无文档解析。对比 RAGFlow / Dify 的差距是**结构性**的，集中在四件事：

1. **解析**（PDF/DOCX/HTML/Markdown，版面/表格/标题层级）——现在只能贴纯文本。
2. **结构分块**（标题感知、父子块、表格不切碎）——现在是定长滑窗。
3. **检索**（向量索引 + 关键词混合 + 重排 + 元数据过滤）——现在是全表取回 + 余弦排序。
4. **生命周期**（增量重入、重嵌入、异步任务、引用溯源、检索评测）——现在改一篇文档只能删了重传。

### 1.1 三条最高性价比

| 优先 | 动作                                                                               | 为什么先做                                                  | 工期       |
| ---- | ---------------------------------------------------------------------------------- | ----------------------------------------------------------- | ---------- |
| 1    | 知识库 pgvector + HNSW + 混合检索（向量 + `tsvector`/`pg_trgm` + RRF）+ 元数据过滤 | 把「能不能用」变成「准不准」；qps/成本随数据线性恶化的根因  | 8–12 人日  |
| 2    | 知识库结构化分块 + 最小解析（markdown/html/txt/pdf/docx）                          | 分块质量决定检索上限，检索算法救不回被切碎的内容            | 8–14 人日  |
| 3    | 记忆候选集索引化 + LLM 巩固 + 结构化工作记忆                                       | 修正 200 条候选的漏召回；把「压缩」从丢信息变成可续跑的状态 | 10–15 人日 |

## 2. 知识库：主流方案对标

### 2.1 能力矩阵

| 能力        | RAGFlow [R1]                                                  | Dify [R2]                                     | LlamaIndex [R3]                                                                        | 本项目现状                                                                             |
| ----------- | ------------------------------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| 文档解析    | DeepDoc：版面分析、OCR、表格识别（CPU 推理）                  | 通用分块 + 可选父子分块；PDF/Word/Markdown 等 | `SimpleDirectoryReader` + 可插拔 reader（unstructured、docling…）                      | **无**；`DocumentCreate.content` 只收纯文本（`knowledge/schemas/knowledge.py:10-15`）  |
| 分块        | 模板化（General/QA/Paper/Book/Table/Resume…）+ 可视化人工干预 | General / Parent-child                        | `SentenceSplitter`、`SentenceWindowNodeParser`、`SemanticSplitterNodeParser`、层级节点 | `chunk_text(size=400, overlap=60)` 段落聚合 + 滑窗（`knowledge/services/chunking.py`） |
| 向量索引    | 稠密 + 稀疏（关键词）双路                                     | 向量库（Qdrant/Milvus/pgvector…）             | 任意 `VectorStore`                                                                     | **JSON 列 + Python 全量 cosine**，无索引                                               |
| 关键词/混合 | 混合检索（向量 + 全文）                                       | 检索模式可选：向量 / 全文 / 混合（含权重）    | `QueryFusionRetriever`（RRF/相对分数）                                                 | 无                                                                                     |
| 重排        | 内置 rerank 模型位                                            | Rerank 模型可选                               | `NodePostprocessor`（Cohere/SentenceTransformer…）                                     | 无                                                                                     |
| 元数据过滤  | 数据集/文档元数据、知识编译产物                               | 元数据过滤 + 条件                             | `MetadataFilters`（eq/in/范围…）                                                       | 无（`source` 只是字符串，不参与过滤）                                                  |
| 增量与同步  | 增量同步数据源（如 BigQuery）、文档级重解析                   | 文档更新重建索引                              | `IngestionPipeline` 的 `docstore` 去重（`doc_id + hash`）                              | 无更新 API，只能删后重传                                                               |
| 引用溯源    | 分块可视化、命中原文定位                                      | 引用与归属（Citation and Attribution）        | `source_nodes` + metadata                                                              | 只回 `document_id/title/ordinal/content`，无页码/标题路径                              |
| 评测        | 检索测试 + 端到端                                             | 召回测试                                      | `RetrieverEvaluator`（hit rate/MRR/NDCG）                                              | 无                                                                                     |
| 治理        | 团队/数据集权限、配额                                         | 成员/租户、速率与存储配额                     | 无（库不管）                                                                           | 仅账号隔离，无配额                                                                     |

### 2.2 现网口径（改造前基线，2026-11-22 审计）

> K1/K2 已于 2026-11-22 落地（见 §5.1 各自的「已落地」说明）：检索不再全量取回进程内打分，默认走 pgvector + 混合检索。下表的差距评级保留为改造前快照。

- 写入：`ingest_document` 分块 → 逐块 embedding → 落 `document_chunks`，**正文只存分块**（文档表无 content 列），改一篇 = 删旧建新。
- 检索：`searchable_rows(account_id)` 取账号**全部** chunk（`knowledge/repositories.py:48-58`），Python 里 `cosine_similarity` 排序后截 `limit`（默认 5，上限 20）。
- 嵌入：默认 `LocalHashingEmbeddingProvider`（blake2b → 1024 维 L2 归一，中文按单字），**不是语义嵌入**（`knowledge/services/embeddings.py` 注释已注明）。
- 对外只有 6 个端点（`knowledge/api/v1/knowledge.py`）：登记/列表/详情/取正文/删除/检索；`SearchRequest` 只有 `query` 与 `limit`。
- 消费面：`search_knowledge` 工具（`services/tool-service/tool_service/services/catalog/remote_tools.py:7-31`）只暴露 `query` + `limit≤10`；chat 另有 `context_refs` 的 `knowledge_document` 显式引用（`services/agent-orchestrator/agent_orchestrator/services/context_refs.py:391`，按 id 取正文，上限 10 条 / 20k 字符），但**没有基于用户问题的自动检索**——模型不调工具、用户不 @ 文档，知识库就等于不存在。

### 2.3 四个结构性差距的影响

| 差距              | 直接后果                                                                                                  |
| ----------------- | --------------------------------------------------------------------------------------------------------- |
| 无索引 + 全量取回 | 10 万 chunk 时每次检索都要把全表传回进程内打分；随账号与文档数增长，延迟与内存线性恶化                    |
| 定长滑窗分块      | 标题与正文被切开、表格被切碎、跨块指代丢失；召回命中却答不对                                              |
| 无混合检索/重排   | 专有名词（型号、合同号）召回差；`local-hash-256` 下语义近乎随机——**默认配置的检索质量不及关键词检索**     |
| 无生命周期        | 文档纠正后无法增量重建；换 embedding 模型等于全库失效（记忆侧已有 `/memories/reembed`，知识库没有对应物） |

## 3. 记忆：主流方案对标

### 3.1 能力矩阵

| 能力      | mem0 v3 [R4]                        | Zep / Graphiti [R5]                                            | Letta [R6]                              | Cognee [R7]                           | LangMem [R8]                                              | 本项目                                                                   |
| --------- | ----------------------------------- | -------------------------------------------------------------- | --------------------------------------- | ------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------ |
| 短期      | 宿主负责                            | 宿主负责                                                       | 会话内 compaction                       | 会话蒸馏                              | 宿主负责                                                  | **压缩摘要**（`conversation_summaries`，原文不删）                       |
| 长期结构  | 事实条目 + 实体链接                 | 时序知识图谱（episode → 实体/边）                              | memory blocks（core）+ archival（向量） | 图 + 向量 + 代码图                    | 条目（`BaseStore` 命名空间）                              | 条目（`memories` 表）                                                    |
| 时间      | 时间推理（当前态/过去/计划）        | **双时态**：`valid_at/invalid_at` + `created_at`，事实变化留边 | block 内文本                            | 时间元数据                            | 无                                                        | `valid_from/invalid_at/superseded_by` 单链，**无边级时间与 ingest 时间** |
| 写入      | ADD-only 累积（不再覆盖）+ 实体抽取 | 增量 episode 集成，不重算全图                                  | agent 自编辑 block                      | `add → cognify → improve`（反馈回灌） | hot-path 工具 + **后台 memory manager**（抽取/合并/更新） | 管理 API + 3 工具 + 抽取（默认关）+ 提交后回调                           |
| 检索      | 混合（语义 + BM25 + 实体加权）      | 图遍历 + 向量 + 全文 + rerank                                  | block 常驻 + archival 向量              | 图/向量/关键词多路                    | 工具 + 语义                                               | BM25 0.65 + 向量 0.35 + pinned/recency + MMR，**候选 200 条内存打分**    |
| 巩固/遗忘 | 平台侧合并                          | 边失效（不删）                                                 | sleep-time agents 后台整理              | `improve`                             | 后台合并                                                  | 词面 0.92 软删 + TTL 清扫                                                |
| 隔离      | `user_id/agent_id/run_id` 命名空间  | `group_id` 图分区                                              | agent 级                                | 数据集/租户                           | `namespace`                                               | `account_id` + `workspace` 两级（**同构且更严**）                        |
| 评测      | 平台评测集                          | 论文级基准                                                     | 评测套件                                | 无                                    | 无                                                        | `evals/retrieval_quality.py`（9 查询，recall@3/MRR/冗余）                |

### 3.2 值得学、也该补的三件事

1. **索引化召回**（mem0/Zep 的默认路径）：向量索引 + 关键词索引做**候选生成**，打分只在候选集内做。现在 `list_active(limit=200)` 充当候选生成，等价于「按更新时间取最近 200 条」——用户三个月前写的偏好会永久失联。
2. **后台语义合并**（LangMem memory manager / Letta sleep-time / Cognee improve）：用一个低成本模型在空闲时把同主题多条记忆合并、消解矛盾、摘要长笔记，而不是纯词面软删。记忆越多，这一步的价值越大。
3. **结构化工作记忆**（Letta block / LangGraph state）：压缩摘要现在是自由文本，模型续跑时拿不到「目标 / 约束 / 待办 / 已完成」这类硬状态；对长任务来说，丢掉的是状态而不只是措辞。

**不必补的**：mem0 的 ADD-only 累积（本项目已有替代链，更可审计）、跨用户共享（`account` 即单人边界，产品未定义分组）、独立记忆服务（D1 的结论仍成立）。

## 4. 不照搬的部分（约束与取舍）

| 主流做法                               | 为什么不照搬                                              | 替代                                                                                     |
| -------------------------------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 独立向量库（Milvus/Qdrant/ES）         | 多一套有状态服务要备份/扩容/鉴权；现网已在同 VPC 有 PG 15 | **pgvector + HNSW**，与业务表同库同事务；预留 `VectorStore` 协议，量大再换               |
| 默认开启 GraphRAG / 全量知识图谱       | 建图成本 ≈ 数据量 × LLM 调用；多账号下成本不可控          | 先做**元数据过滤 + 混合检索 + rerank**，图作为 P2 可选层（按数据集开关）                 |
| rerank 作为硬依赖（Cohere/BGE 云服务） | 引入外部 SLA 与新失败面；离线/测试必须零网络              | `/v1/rerank` 走 llm-gateway，失败自动降级为 RRF 顺序；本地 `bge-reranker` 作为可选提供方 |
| 用大模型做在线合并（每次写入）         | 写路径延迟与成本翻倍                                      | 合并放**后台**（sweeper/sleep-time），写路径只做确定性校验                               |
| 语义分块模型（embedding 断点）         | 需要长上下文嵌入模型，成本高、可解释性差                  | 结构分块（标题/表格/列表）+ 定长兜底，先解决 80% 问题                                    |

## 5. 改造清单

> 每条包含：改什么（文件级落点）、契约变化、迁移/测试、验收。P0 = 本轮必做，P1 = 下一轮，P2 = 观察。

### 5.1 知识库 P0

#### K1 向量索引：JSON 列 → `pgvector` + HNSW

> **已落地（2026-11-22）**：维度是**代码常量** `knowledge/core/vectors.py:EMBEDDING_DIM = 1024`（改维度必须迁移 + 重嵌入，不做 env）；`DocumentChunk.embedding` 改 `Vector(1024)` 且可空（NULL = 待重嵌入）；迁移 `migrations/versions/b7d4c2a19f30_pgvector_embedding.py` 建 `vector`/`pg_trgm` 扩展 + JSON→vector 回填（仅 1024 维，其余置 NULL）+ `ix_document_chunks_embedding_hnsw`（m=16, ef_construction=64）+ `ix_document_chunks_content_trgm`（GIN gin_trgm_ops）；仓储新增 `vector_statement`/`keyword_statement` 与 `vector_candidates`/`keyword_candidates`，检索候选由 SQL 取（`knowledge/repositories.py`），SQLite 无 pgvector 时同一列类型按文本存取、退回进程内余弦。未引入 `KNOWLEDGE__VECTOR_BACKEND`/`EMBEDDING_DIM` env（SQLite 靠类型实现跑通，少一个配置分支）；`/knowledge/reembed` 仍属 K5。

- **落点**：`knowledge/models/knowledge.py`（`DocumentChunk.embedding` 改为 `Vector(dim)`，新增 `embedding_dim`/`updated_at`）；`migrations/versions/` 新迁移（`CREATE EXTENSION IF NOT EXISTS vector` + 回填 + `CREATE INDEX ... USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)`）；`knowledge/repositories.py` 新增 `nearest_chunks(account_id, embedding, limit, filters)`；`knowledge/services/knowledge.py` 的 `search` 改为「索引取候选 → 候选内精算」。
- **契约**：`SearchResponse` 增 `elapsed_ms`；`SearchHit.score` 语义改为「融合分」；`KNOWLEDGE__EMBEDDING_DIM` 默认 1024（bge-m3），`KNOWLEDGE__VECTOR_BACKEND=pgvector|python` 双实现保留一版做回滚。

  **实际口径**：`SearchResponse` 增 `mode` + `elapsed_ms`；三种 `mode` 的 score 语义不同（vector=余弦、keyword=词法覆盖率、hybrid=RRF 分，通常 0~0.03，不可跨查询比较），已写进 `knowledge/schemas/knowledge.py` 模块 docstring。

- **前置校验**：目标 PG 实例可 `CREATE EXTENSION vector`（腾讯云 PG 15 需确认；不可用则走「自建 PG 镜像 + 扩展」或退到 `pg_trgm + tsvector` 先做关键词检索）。**这是本轮唯一的外部阻塞项，先验证再排期。**
- **测试**：SQLite 路径必须继续可跑——用 `VECTOR_BACKEND=python` 的 fake 索引（现有 `cosine_similarity` 保留为参考实现），PG 集成测试用 testcontainers `pgvector/pgvector:pg16` 镜像。
- **验收**：1 万 chunk 下 P95 检索 < 150ms；召回结果与暴力解 top-20 的 recall@20 ≥ 0.95；`pgvector` 不可用时自动降级且日志可见。

#### K2 混合检索 + 元数据过滤

> **已落地（2026-11-22）**：关键词路不走 `tsv`——`to_tsvector('simple')` 把连续中文当一个 token，不装 `zhparser` 就基本不可用；改用 **pg_trgm + ILIKE 粗筛 + 进程内词法打分**（`knowledge/services/lexical.py`：中文二元组 + 单字回退、拉丁词/数字，覆盖率 0.9 + 频次 0.1），SQLite 与 PG 排名一致可复现。`knowledge/services/search.py` 实现三种模式 + `fuse()` RRF（k=60、候选 50、向量权重默认 0.5），默认 `mode=hybrid`；向量零相似度候选不参与融合。`SearchRequest` 已增 `mode`/`vector_weight`，`filters` 与 `tags`/`content_hash` 仍未做。

- **落点**：`DocumentChunk` 增 `tsv tsvector`（`to_tsvector('simple', content)`）+ GIN 索引（中文先按字符切词再交给 `simple` 配置（或 `pg_trgm` 三字组相似度兜底，`zhparser`/`pg_jieba` 若可用则优先））；`knowledge/services/search.py`（新）实现「向量 topN + 关键词 topN → RRF(`k=60`) 融合」；`KnowledgeDocument` 增 `tags JSON`/`metadata JSON`/`content_hash`。
- **契约**：`SearchRequest` 增 `filters`（`source`/`tags`/`created_after`）、`mode=hybrid|vector|keyword`、`vector_weight`（默认 0.6）、`rerank`；`search_knowledge` 工具 schema 同步扩参（`remote_tools.py`），描述里写清「何时用关键词」。

  **实际口径**：已增 `mode`（默认取 `KNOWLEDGE__SEARCH_DEFAULT_MODE=hybrid`）与 `vector_weight`（默认 `KNOWLEDGE__SEARCH_VECTOR_WEIGHT=0.5`，请求可覆盖）；`filters`/`rerank` 与工具 schema 扩参未做。

- **验收**：golden 30 条查询上，hybrid 的 recall@5 比纯向量 ≥ +15pp、比纯关键词 ≥ +10pp（`local-hash` 与 `bge-m3` 各跑一遍）。

#### K3 结构化分块 + 最小解析

- **落点**：新增 `knowledge/services/parsing/`（`text.py`/`markdown.py`/`html.py`/`pdf.py`/`docx.py`，统一 `ParsedDocument(text, sections[], pages[])`）；`chunking.py` 重写为 `chunk_document(parsed, strategy)`：按标题层级建**父块**（章节摘要，400–800 字）与**子块**（200–400 字，表格整块不切），子块带 `heading_path/page/char_range`；定长滑窗降级为兜底策略。
- **契约**：`DocumentCreate` 增 `content_type`（`text/markdown/html/pdf/docx`）与 `source_url`；`DocumentChunk` 增 `heading_path`/`page`/`char_start`/`char_end`/`parent_id`；检索结果带 `heading_path`（引用可定位）。
- **依赖**：`pypdf`（纯 Python，体积小）优先，PDF 版面/表格识别不上（那是 DeepDoc 量级的工作，P2 再评估 `docling`）；docx 用 `python-docx`。两者都要登记到 `services/knowledge-service/pyproject.toml` + `npx nx run knowledge-service:sync`。
- **验收**：markdown 标题不跨块、表格不被切断（单测断言）；同文档的 `heading_path` 可还原原文。

#### K4 可选重排

- **落点**：`services/llm-gateway` 新增 `POST /v1/rerank`（litellm `arerank`，默认走 litellm 已支持的 rerank provider，模型名由 `RERANK_MODEL` 配置；失败按 `llm.rerank_error` 502）；`knowledge/services/rerank.py` 客户端 + 超时 + 降级；`KNOWLEDGE__RERANK_ENABLED`（默认 false，P0 只打通链路）。
- **验收**：开启后 golden recall@5 再提升（记录数字），关闭时行为逐字不变；网关不可用 → 直接返回 RRF 顺序且 `response.rerank="skipped"`。

### 5.2 知识库 P1

| #   | 改造           | 落点                                                                                                                                            | 验收                                           |
| --- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| K5  | 增量与重嵌入   | `content_hash` 去重；`PUT /documents/{id}` 重建分块；`POST /knowledge/reembed`（对齐 `/memories/reembed`）                                      | 同文档重入不产生重复 chunk；换模型后可整库重建 |
| K6  | 异步 ingestion | 复用 [background-jobs.md](./background-jobs.md) 语义：`POST /documents` 返回 `status=processing`，`GET /documents/{id}` 轮询；大 PDF 不阻塞请求 | 100 页 PDF 请求 < 200ms 返回                   |
| K7  | 引用溯源       | 命中返回 `heading_path/page/char_range` + `GET /documents/{id}/content?cursor=`                                                                 | 前端可跳到原文位置                             |
| K8  | 检索评测       | `services/activity/knowledge/evals/`（对齐记忆的 `evals/retrieval_quality.py`），30 条 golden + CI 断言                                         | 每次改检索都要过基线                           |
| K9  | 配额与集合     | `collection` 表或 `document.collection`；账号级文档数/字节配额（照 [rate-limiting.md](./rate-limiting.md) 口径）                                | 超限 429/400 语义明确                          |

### 5.3 知识库 P2（观察，不排期）

- RAPTOR 摘要树 / 知识编译（RAGFlow 1.0 的 Wikis/Graphs/PageIndex 思路）——先等 K2/K8 有数据再评估。
- 数据集级知识图谱（实体/关系）——按数据集开关，成本封顶。
- 多模态（图片 OCR、表格结构）——评估 `docling`/DeepDoc 自托管体积。
- URL / 连接器增量导入（GitHub、Notion、飞书）——复用 connector-service 凭证，不新建抓取栈。

### 5.4 记忆 P0

#### M1 候选集索引化

- **落点**：`memories.embedding` JSON → `Vector(1024)`（与知识库共用同一扩展，一次性迁移）；`MemoryRepository.list_active` 保留（管理面用），新增 `candidate_ids(account_id, query_embedding, limit=200, filters)` 走 HNSW；`search_memories` 改为「向量候选 ∪ BM25 候选 → 200 条内精算」，去掉「最近 200 条」的隐式截断。
- **契约**：无 API 变化；`memory_embedding_provider` 默认从 `local` 改为 `gateway`（生产必须，本地开发仍可 `local` 并用 fake 索引）。
- **验收**：2000 条记忆下召回与暴力全量解 recall@5 ≥ 0.95；三个月前写入的相关记忆可被召回（新增回归用例）。

#### M2 后台语义巩固（替代纯词面软删）

- **落点**：`services/memory_sweeper.py` 增加 `consolidate_semantic`：同 kind + 同 scope + 向量相似 ≥ 0.85 的簇，用一次低成本模型调用产出「合并文本 + 置信度」，写新条并把旧条置 `invalid_at/superseded_by`（保留审计链），失败保持原状；受 `memory_consolidation_provider`（`local` 保持现行为）控制，默认仍是词面。
- **约束**：单账号单轮上限（如 5 簇）、敏感过滤复用 `find_sensitive`、失败只 warning。
- **验收**：构造 20 组近义记忆，合并后条数下降且召回不降（golden 集）；关闭开关时行为逐字不变。

#### M3 压缩摘要结构化

- **落点**：`services/compaction.py` 摘要输出改为「结构化摘要 + 自由文本」：`{goal, constraints[], decisions[], open_tasks[], entities[]}` 存 `conversation_summaries.structured JSON`（新列），注入时渲染成紧凑文本（仍受 `summary_max_chars` 预算）；失败回退纯文本。
- **验收**：长任务（≥40 消息）续跑时，golden 用例对「当前目标/未完成项」的命中率提升；token 预算不升。

### 5.5 记忆 P1

| #   | 改造                 | 落点                                                                                                                                          | 验收                                      |
| --- | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| M4  | 实体/关系 + 边级时间 | 新表 `memory_entities`/`memory_edges`（`valid_at/invalid_at/ingested_at` + `source_memory_id`），召回时 1 跳扩展（Graphiti 思路，不做全量图） | 「谁负责 X」多跳问题可答；边失效不删      |
| M5  | 记忆版本             | `memory_versions`（content/confidence/操作者/时间），PATCH 时留版本                                                                           | 可 diff、可回滚                           |
| M6  | 衰减与优先级         | 打分加入 importance（人工/使用频次）与指数衰减，替代固定 `_RECENCY_BONUS=0.05`                                                                | golden 集排序不变差；长期未用条目自然降权 |
| M7  | sleep-time 后台      | 空闲窗口做抽取/合并/摘要（复用 sweeper 单飞锁）                                                                                               | 不增加对话延迟                            |
| M8  | 评测扩展             | `evals/retrieval_quality.py` 扩到 40+ 查询；加「时间推理」「冲突消解」两类断言                                                                | 成为改打分的门禁                          |

### 5.6 跨模块改动

| 项                        | 落点                                                                                           | 说明                                                                                           |
| ------------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `/v1/rerank` 网关端点     | `services/llm-gateway/llm_gateway/api/v1/`、`services/embeddings.py` 旁同级                    | 知识库 K4 与记忆 M6 共用；需同套内部密钥与 502 契约                                            |
| `search_knowledge` 参数面 | `services/tool-service/tool_service/services/catalog/remote_tools.py`、`executors/http.py:203` | 增 `mode/filters/rerank`，保持 `READ` 风险级与 20s 超时                                        |
| chat 自动检索（可选）     | `services/agent-orchestrator/.../chat*.py`                                                     | 现只有工具调用才检索；可在「知识库非空」时做一次廉价预检索注入（类似记忆召回块），需预算与开关 |
| 前端                      | `apps/web/src/features/knowledge/*`、`features/memory/*`                                       | 知识库：上传文件、分块预览、标签/集合、引用跳转；记忆：图视图/时间线（P2）                     |

## 6. 里程碑建议

| 波次       | 内容                                                      | 出口标准                                                 |
| ---------- | --------------------------------------------------------- | -------------------------------------------------------- |
| W1（2 周） | K1 + K2 + 迁移 + golden 评测（K8 先行，先有尺子再改算法） | 1 万 chunk P95 < 150ms；hybrid 优于纯向量/纯关键词有数字 |
| W2（2 周） | K3 + K4 + K5                                              | 上传 PDF/Markdown 可用；重入幂等；rerank 可开关          |
| W3（2 周） | M1 + M3 + M2（默认仍词面）                                | 记忆召回回归通过；结构化摘要上线                         |
| W4（按需） | K6/K7/K9 + M4/M5                                          | 异步入库、引用定位、实体多跳                             |

## 7. 验收指标（写进 CI 或周报）

- 知识库检索：recall@5、MRR、P95 延迟、rerank 增益（有/无对比）；`local-hash` 与 `bge-m3` 分列。
- 记忆检索：现有 `retrieval_quality` 的 recall@3/MRR/前两名冗余**不得回退**；新增「超 200 条候选」「跨月记忆」回归。
- 成本：每个账号的 embedding/rerank token 与调用次数（走 `cost-attribution` 口径）。
- 覆盖率门禁：knowledge 90 / agent-orchestrator 82 不回退；新模块按同档。

## 8. 参考

- [R1] RAGFlow README（DeepDoc 版面/OCR/表格识别、模板分块、可视化分块、Knowledge Compilation）— https://github.com/infiniflow/ragflow
- [R2] Dify 知识库文档（检索设置、混合检索权重、Rerank、元数据过滤、引用归属）— https://docs.dify.ai/en/cloud/use-dify/knowledge/integrate-knowledge-within-application
- [R3] LlamaIndex（IngestionPipeline、SentenceWindow/AutoMerging/Recursive 检索、MetadataFilters）— https://docs.llamaindex.ai/
- [R4] mem0 How It Works / Platform v3 迁移（ADD-only 累积、混合检索含 BM25 与实体加权、实体链接、时间推理）— https://docs.mem0.ai/core-concepts/how-it-works
- [R5] Graphiti（双时态上下文图、episode、增量集成、provenance）— https://github.com/getzep/graphiti
- [R6] Letta memory blocks / archival memory / sleep-time（docs "Memory blocks (core memory)"）— https://docs.letta.com/guides/agents/memory-blocks
- [R7] Cognee（图 + 向量记忆、session distillation、improve）— https://github.com/topoteretes/cognee
- [R8] LangMem（hot-path 工具 + background memory manager）— https://github.com/langchain-ai/langmem
- [R9] pgvector（HNSW/IVFFlat 参数、halfvec/sparsevec、iterative scan）— https://github.com/pgvector/pgvector
