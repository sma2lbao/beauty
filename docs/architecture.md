# beauty-moon 微服务架构设计

> 状态:**定稿 v1.0** · 2026-10-12
> 范围:AI agent harness 产品(WorkBuddy 类)的后端微服务平台
> 本文是唯一架构事实源;实现与本文冲突时,先修订本文达成共识,再动代码。

## 1. 产品定位

**替用户在真实账号里把活干完**的 AI 办公 harness,对标腾讯 WorkBuddy 与字节豆包工作:

- 第一连接器:**邮箱**(Gmail API / Microsoft 365 Graph / 国内 IMAP+授权码),服务国内+海外双市场;
- 渠道:**web 独立站先行**,移动 app 后置,共用同一套 `/api/v1` 契约;
- 双模式交互:**即时问答**(轻路径,单次 LLM 调用,不建 run)与**长任务执行**(重路径,durable run);
- 差异化:邮箱场景纵深 + 数据驻留/自托管可控性,不拼通用办公广度;
- 现有 `apps/ink` 将废弃,不在迁移路径上投入。

## 2. 总体架构

```text
apps/web (React 19, 画布+时间线+SSE)
        │
   gateway :8080 (SSE 透传/限流/版本化路由)
        │
┌────────────────────── 控制面 ──────────────────────┐
│ agent-orchestrator:会话与运行编排                  │
│   LangGraph 状态机 · Postgres checkpoint           │
│   人审中断 · SSE · 技能注册表 · 指令队列(含 runner) │
└───────────────┬────────────────────────────────────┘
                │ HTTP/gRPC(起步) + 事件(Redis Streams)
┌───────────────┴───────────── 数据面 ────────────────────────────┐
│ connector-service   邮箱适配器 + OAuth/授权码 token vault         │
│ workspace-service   云沙箱(E2B/容器实例) + artifacts → COS      │
│ knowledge-service   摄取/检索(pgvector)/记忆                    │
│ llm-gateway         模型+embedding 唯一出口(LiteLLM)            │
└──────────────────────────────────────────────────────────────────┘
事件总线(events):run.* / email.received.* → 计量/知识库/通知 消费
观测:OTel(GenAI semconv) → Langfuse;usage_ledger → credits/billing
```

核心原则:

1. **durable execution**:长任务状态落库、可中断、可恢复、可人审;
2. **一切事件化**:过程 = 事件流,可回放、可检索、可审计;
3. **LLM 访问唯一出口** llm-gateway:路由/降级/配额/预算/缓存,业务服务禁止直连模型 SDK;
4. **业务逻辑 transport 无关**:graph 节点与业务层不感知 HTTP/gRPC/MCP,接口层都是薄壳。

## 3. 服务清单(阶段 A:六个)

| 服务 (`services/`) | 职责 | HTTP 端口 |
|---|---|---|
| `account-service` | 用户/组织/配额/套餐 + sessions/devices;认证交给 IdP(Logto 自托管) | 8001 |
| `agent-orchestrator` | 会话与运行编排:graph/checkpoint/SSE/技能注册表/简单定时(含 runner) | 8002 |
| `llm-gateway` | 模型+embedding 路由(LiteLLM):区域路由/降级/预算/限流/缓存 | 8003 |
| `connector-service` | 邮箱适配器、token vault、以 MCP 暴露工具 | 8004 |
| `workspace-service` | 云沙箱生命周期(E2B/容器实例)、artifacts → 对象存储 | 8005 |
| `knowledge-service` | 摄取(事件驱动)+ 检索(pgvector)+ 记忆(起步合体) | 8006 |

gRPC 端口规划 `90xx` 与 HTTP 同号对应,引入时机见 §5。

- **阶段 B**:browser-service、runner、scheduler、guardrails、memory 拆分;实时推送(Gmail Pub/Sub / Graph webhooks / IMAP IDLE);canary 发布;evals 进 CI 门禁。
- **阶段 C**:billing/支付、通知中心、移动 app+推送、开放平台(MCP 对外技能)、k8s、第二区域部署。

## 4. Monorepo 结构与命名

```text
beauty-moon/
├── apps/
│   └── web/                     # 产品前端(画布+时间线+artifacts 面板,反聊天刷屏)
├── services/                    # ★ 微服务:部署单元
│   ├── _template/               #   服务脚手架模板(uv workspace 需 exclude)
│   ├── account-service/
│   ├── agent-orchestrator/
│   ├── llm-gateway/
│   ├── connector-service/
│   ├── workspace-service/
│   └── knowledge-service/
├── packages/                    # ★ 共享库:实名命名(参考 design 包,不用 py- 前缀)
│   ├── common/                  #   错误模型/配置/日志/认证依赖/观测/SSE
│   ├── database/                #   DeclarativeBase/命名约定/Repository/UoW/testcontainers
│   ├── events/                  #   事件信封 + 事件总线抽象 + outbox
│   ├── agent-kit/               #   MCP 封装/GenAI trace/agent 事件 payload(阶段 A 后期)
│   ├── clients/                #   OpenAPI 生成 SDK(第二个消费者出现时启用)
│   └── contracts/               #   proto 契约 + buf 生成物(内部 gRPC 化时启用)
├── deploy/
│   ├── docker-compose.yml       # 本地 dev:pg/redis/logto/litellm/langfuse + 服务
│   └── regions/                 # 国内/海外区域差异配置(数据驻留)
└── tools/                       # eslint-config 等
```

**放置判据**:

- `services/`:独立部署单元——有 Dockerfile、被 compose/k8s 编排的长驻进程(暴露 API 或消费队列,纯 worker 也算);
- `apps/`:人直接使用的终端 UI;
- `packages/`:被 import 的库(不部署)。

**命名护栏**(Python 顶层 import 没有作用域):

- 避开 stdlib 模块名(如 `platform`)与主流库顶层名(如 `agents` 已被 openai-agents 占用,故用 `agent-kit`);
- 逃生舱:将来可用 PEP 420 命名空间包 `beauty.*`(对应 JS 侧 `@beauty/` 作用域语义)。

**uv workspace 注意**:members 为**显式清单**——`apps/` 与 `packages/` 混有 npm 项目(web/design/ts-utils 无 `pyproject.toml`),glob 会命中无 pyproject 的目录导致 uv 报错(见 [uv workspace 文档](https://docs.astral.sh/uv/concepts/projects/workspaces/))。新 Python 项目必须在根 `pyproject.toml` 登记 members + dependencies + `[tool.uv.sources]`,随后 `npx nx sync <项目>`;`services/_template` 用 `[tool.uv.workspace] exclude` 排除。

## 5. 通信设计

| 通道 | 协议 | 说明 |
|---|---|---|
| 对外(web/app) | REST `/api/v1` + SSE | OpenAPI 即契约;SSE 断线用 Last-Event-ID 重放 |
| 服务间(起步) | HTTP + OpenAPI 生成客户端 | `packages/clients` 由各服务 schema 生成,统一超时/重试 |
| 服务间(升级) | gRPC(内 gRPC、外 REST) | 触发条件:内部高频/流式/多语言契约;契约集中于 `packages/contracts`(proto+[buf](https://buf.build),`buf breaking` 进 CI);同进程双端口 uvicorn + grpc.aio,业务层 transport 无关 |
| 异步事件 | Redis Streams(→Kafka) | `events` 包:信封 + outbox;阶段 B 引入 |
| 工具调用 | MCP(streamable HTTP) | connector/workspace 以 MCP server 暴露工具,内外共用 |
| 模型访问 | OpenAI 兼容 API | 唯一出口 llm-gateway;按部署区域路由模型 |

## 6. Agent 执行模型(核心)

### 6.1 Graph 固定骨架

```text
plan → confirm → execute → verify → deliver
```

- **plan**:产出**可编辑的 Markdown 计划 artifact**(存对象存储,画布上直接改),不是 yes/no 门;
- **confirm**:人审中断(checkpointer 挂起),用户改完计划确认后再执行;
- **execute**:工具/MCP/连接器调用,产出 artifacts;
- **verify**:对 `acceptance_criteria` **逐项校验**,不达标自动返工,**上限 3 轮**(防死循环烧钱);
- **deliver**:交付物注册;写操作按操作策略执行(见 §6.4)。

### 6.2 Run 状态机(goal 语义)

```text
pending → planning → awaiting_approval → running → awaiting_confirmation
                        │                    │(外部条件不满足)        │
                        └── resume ── blocked(reason)                  ├→ completed / failed
```

- `rounds` 计数:支持跨轮续跑与有界返工;
- `blocked(reason) → resume`:覆盖"等邮件回复/等外部事件"类场景;
- 即时问答走轻路径,不建 run。

### 6.3 事件溯源(run_events)

- `run_events` 表:append-only、`seq` 单调编号、`type` + `payload`(JSONB),**落 Postgres**;
- Redis Streams 只做实时分发;**SSE 断线回放从 Postgres 读**;
- 事件名复用 `events` 信封约定:`run.started.v1` / `token.delta.v1` / `tool.invoked.v1` / `run.completed.v1` …;
- 价值:审计流水 + 断线/多端回放 + 全文检索——"AI 工作记录可回放可检索"是产品功能。

### 6.4 操作策略

per-connector / per-tenant 配置,每个动作分级 `auto | confirm | deny`:

- 读操作默认 auto;写/发/删默认 confirm;危险删除默认 deny;
- 用户可按连接器调档到"全自动"——策略是配置,不是硬编码。

### 6.5 指令追加队列

run 执行中用户可继续发指令,入 `pending_instructions`,当前 run 完成后依次触发(豆包"任务队列"语义)。

### 6.6 引擎与上下文

- 执行引擎默认 **LangGraph + Postgres checkpointer**;流程复杂/强 SLA/大量人审编排时升级 **Temporal**;**不自研状态机**;
- graph 内设 **context 组装/压缩节点**;超长任务轮次间只传**有界结构化摘要**(借鉴 DSH ralph 与 [Manus 上下文工程](https://www.zenml.io/llmops-database/context-engineering-for-production-ai-agents-at-scale))。

## 7. 数据模型要点

| 表/存储 | 归属 | 说明 |
|---|---|---|
| runs / run_events / pending_instructions | orchestrator | §6 状态机、事件溯源、指令队列 |
| artifacts | workspace | 交付物元数据;实体文件在对象存储(COS/R2) |
| connections + credentials | connector | 双凭据模型:OAuth refresh token 与国内授权码/密码;KMS 加密 |
| skills | orchestrator | 技能 = prompt 模板 + 工具集 + 输入 schema + 触发方式 + 交付物类型;版本化 |
| schedules | orchestrator(阶段 B 独立) | cron/webhook 触发器 |
| usage_ledger | 计量消费者 | 模型 token + 沙箱分钟 + 工具调用 → credits |
| feedback | orchestrator | 点赞/点踩/纠错 → datasets 飞轮 |
| 知识库(pgvector) | knowledge | 全表 tenant_id,检索同租户过滤 |
| sessions/devices | account | 多端会话(为 app 预留) |

## 8. 知识、记忆与连接器

- **knowledge-service**(阶段 A 合体):ingestion 消费 `email.received.v1` 等事件 → embedding(**经 llm-gateway 走 API**,自托管 bge-m3 后置)→ pgvector;retrieval 提供检索+重排入口;memory 以表起步(事实/偏好/会话摘要);
- **邮箱连接器**:Gmail API(OAuth)、M365 Graph(OAuth,日历将来顺手)、国内 IMAP/SMTP(授权码);同步 = 游标增量 + 轮询起步,实时推送阶段 B;附件不进库,落 COS 由沙箱按需解析;
- 首批技能:邮件摘要、智能回复草稿、分类去噪、邮件→周报、附件归档检索;写/发类动作默认人审。

## 9. 安全与合规

1. **token vault**:KMS 加密、租户隔离、最小 OAuth scope、全程审计——用户真实账号令牌是安全皇冠;
2. **操作分级**(§6.4)+ 每租户工具白名单;
3. **沙箱强隔离**(E2B/容器实例),不可信代码不进服务进程;
4. **审计**:run_events 即审计流水(谁、何时、在谁的账号、做了什么、结果如何,不可抵赖可回放);
5. **数据驻留**:国内/海外双区域,**同一套代码**、区域决定模型路由与数据存储位置;国内区只路由境内可用模型(如 DeepSeek)。

## 10. 可观测与质量

- 全链路 OTel(GenAI 语义约定仍在演进,用 openllmetry instrumentation 起步),trace 贯穿 web → orchestrator → 工具 → 模型;
- [Langfuse](https://github.com/langfuse/langfuse) self-host(trace/prompt 管理/datasets/evals;OTel 原生备选 Arize Phoenix),按租户/功能打标;
- **evals 是发布门禁**:golden datasets + [ragas](https://github.com/explodinggradients/ragas) 挂 CI,改 prompt/模型/图结构必须过基线;
- 用户反馈入库,周期性回灌 datasets;
- credits:usage 事件 → usage_ledger → billing(阶段 C)。

## 11. Web 与 API 纪律

- `/api/v1` 是产品公共契约(未来 app 同套):版本化、分页/错误约定统一(错误统一渲染 `{"code","message","details"}`,见 `packages/common`);
- SSE:网关透传(禁缓冲)+ Last-Event-ID 重放;durable run 天然支持换设备续看进度;
- 信息架构:**画布 + 过程时间线 + artifacts 面板**,计划是画布上可编辑的文档(反聊天刷屏);
- IdP:**Logto** 自托管(同时支持国内短信/社交登录与 Google/Apple,覆盖将来 iOS app)。

## 12. 工程约定

- 命令纪律见 `AGENTS.md`:Python 依赖只经 `nx run <项目>:add`,变更后 `npx nx sync <项目>`;uv.lock 不手改;
- 端口:服务 HTTP `80xx`(8001 起)/ gRPC `90xx` 同号对应 / 网关 8080;web dev 5173 不变;
- 测试:unit(无 IO)+ integration(testcontainers),报告/覆盖率落根 `reports/`、`coverage/`;
- 部署演进:本地 compose → 前期生产**单云主机 compose**(托管 PG/COS/Redis)→ k8s(用户量驱动);
- 代码风格:服务与共享库 ruff line-length 88(与 py-utils 一致);Python 3.12(根 `.python-version`)。

## 13. 竞品对比结论(为什么长这样)

对标字节**豆包工作**(2026-09 上线[目标模式/计划模式/任务队列](https://www.chinaz.com/ainews/31298.shtml)、[多任务排队](https://post.smzdm.com/p/aqrmevvp/))与 **DSH**(DeepSeek Harness,一手源码+运行时观察)后沉淀的关键决策:

| 模式 | 来源 | 本架构落点 |
|---|---|---|
| 计划先行、确认后执行;计划 = 可编辑 artifact | 豆包计划模式 / DSH plan mode | §6.1 |
| 验收标准逐项校验 + 自动返工(有界) | 豆包目标模式 | §6.1 verify |
| 运行中追加指令队列 | 豆包任务队列 | §6.5 |
| goal 语义(rounds / blocked / resume) | DSH goals | §6.2 |
| 事件溯源(seq / 回放 / 检索) | DSH session events | §6.3 |
| approval policy 配置化 | DSH approval/sandbox policy | §6.4 |
| CAS 任务板多 agent 协调 + 写域 | DSH team task board | 阶段 B runner 拆分时引入 |

## 14. 阶段路线

- **阶段 A(当前)**:三个共享包(common/database/events)+ 六个服务 + compose(dev)+ IdP/LLM 网关/观测接入;首批邮箱技能端到端跑通;
- **阶段 B**:browser/runner/scheduler/guardrails/memory 拆分;实时推送;canary;evals 进 CI 门禁;
- **阶段 C**:billing/支付、移动 app+推送、开放平台(MCP 对外)、k8s、第二区域。

## 15. 待定决策

| 决策 | 当前默认 | 决定时机 |
|---|---|---|
| 执行引擎 | LangGraph + Postgres checkpointer | 阶段 A 动工即定;复杂编排再议 Temporal |
| 内部 RPC 化(gRPC) | 不做,HTTP + 生成客户端 | 内部高频/流式出现时 |
| 自托管 embedding | 不做,API 起步 | 检索质量/成本成为瓶颈时 |
| k8s | 不做,单云主机 compose | 并发用户量起来后 |
| guardrails 强度 | 输入过滤起步 | 有真实用户数据前定 |
| 移动 app | 不做 | 阶段 C |

## 16. 参考

- [Manus 用 E2B 提供虚拟机](https://e2b.dev/customers/how-manus-uses-e2b-to-provide-agents-with-virtual-computers) · [Manus 上下文工程](https://www.zenml.io/llmops-database/context-engineering-for-production-ai-agents-at-scale)
- [OpenHands 架构(事件流 + 运行时沙箱)](https://docs.openhands.dev/enterprise/architecture)
- [LangGraph](https://github.com/langchain-ai/langgraph) · [agent-service-toolkit(FastAPI+LangGraph 生产模板)](https://github.com/JoshuaC215/agent-service-toolkit) · [Temporal](https://github.com/temporalio/temporal)
- [LiteLLM](https://github.com/BerriAI/litellm) · [MCP](https://modelcontextprotocol.io) · [Logto](https://github.com/logto-io/logto) · [Langfuse](https://github.com/langfuse/langfuse) · [ragas](https://github.com/explodinggradients/ragas)
- [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) · [buf](https://buf.build)
- 豆包工作 9 月更新:[目标模式/计划模式](https://www.chinaz.com/ainews/31298.shtml) · [任务队列](https://post.smzdm.com/p/aqrmevvp/)
