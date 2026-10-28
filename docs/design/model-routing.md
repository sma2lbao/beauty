# 模型路由契约(per-run 选择 / fallback / 结构化输出 / 多模态)

> 状态:**P1-G 落地(2026-10-28)**,本文件是唯一真源;实现若与本文冲突,以本文为准并先改本文。
> 相关:`docs/architecture.md` §5(模型唯一出口)/§11(统一事件模型)、
> `docs/design/event-taxonomy.md`(过程项)、`docs/gap-2026-10/08-refresh-2026-10-27.md`(P1-G)。

## 1. 为什么

现状(2026-10-27)有三个断点:

1. **选择不可控**:`RunCreate` 没有 `model`,orchestrator 的 `LLMClient` 只能吃
   `LLM_MODEL` 里的一个全局默认;租户/一次任务想换模型只能改服务环境。
2. **单点故障**:llm-gateway 单次调用,上游 5xx/限流/超时即整次失败,没有主备切换,
   也没有"换过模型"的可见记录。
3. **能力受限**:网关不支持 `response_format`(结构化输出只能靠提示词 + 正则),
   附件虽然收图却从不进模型(多模态只是"能上传")。

P1-G 把这三处补齐:选择(per-run / per-conversation)、韧性(gateway 内 fallback
chain + 事件可见)、能力(JSON Schema 输出、图片输入)。

## 2. 目标与非目标

**目标**

- 一次 run 可指定模型(`runs.model`),一段对话可指定模型(`conversations.model`),
  前端可选;
- 网关按 fallback chain 自动切换,切换记录进 run 事件(`llm.fallback.v1`),
  UI/回放可见;
- `response_format` 端到端透传,平台自己用 JSON Schema 输出(意图分流);
- 图片附件以 `image_url`(data URL)进模型,单条消息可问图;
- 本地工具(不进 tool-service 的 `todo_write`/`spawn_subagent`/记忆等)执行前
  用同一套 JSON Schema 校验参数。

**非目标**

- 模型按租户的计费/套餐白名单(归 account 套餐,本期白名单=网关 `AVAILABLE_MODELS`
  平台列表;租户级白名单留后续);
- 自动模型路由(按任务难度挑模型)、多模型投票、模型评测;
- 图片**生成**、视频/音频输入;
- 任务(run)路径把图片送进执行图(本期多模态只覆盖对话路径,见 §8 边界)。

## 3. 分层

```
前端:模型选择器 ──POST message{model}/PATCH conversation{model}──▶ orchestrator
orchestrator:runs.model / conversations.model ──contextvar──▶ LLMClient
LLMClient(GatewayClient) ──POST /v1/completions{model,response_format}──▶ llm-gateway
llm-gateway:白名单校验 → fallback chain(主模型 → fallback_models)→ litellm
```

- **选择**:显式值(消息/run) > 会话值 > 网关默认(`LLM_GATEWAY__DEFAULT_MODEL`)。
- **白名单**:网关 `AVAILABLE_MODELS` 非空时,请求的模型必须在其中,
  否则 400 `llm.model_not_allowed`;为空时只校验 `DEFAULT_MODEL` 存在的语义。
- **fallback**:网关内部行为,调用方无感;切过模型会在响应里带 `fallbacks`,
  由 orchestrator 落 `llm.fallback.v1`。

## 4. 数据模型与迁移

迁移 revision:`e7f3a1c9b5d2`(`model_routing`),`down_revision = "d2c7f5b9a1e4"`。

| 表              | 列      | 类型                   | 说明                                    |
| --------------- | ------- | ---------------------- | --------------------------------------- |
| `runs`          | `model` | `String(120)` nullable | 本次任务使用的模型;NULL=按会话/网关默认 |
| `conversations` | `model` | `String(120)` nullable | 该对话的模型偏好;NULL=网关默认          |

两列都可空、无回填;旧数据继续走默认路由。`runs.model` 是**快照**语义:
run 创建时确定,后续改会话模型不影响已在跑的 run。

## 5. llm-gateway 契约变更

### 5.1 请求(`CompletionRequest`)

```jsonc
{
  "model": "deepseek/deepseek-chat", // 可选;不传走 DEFAULT_MODEL
  "messages": [
    { "role": "user", "content": "图里报什么错?" },
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "图里报什么错?" },
        {
          "type": "image_url",
          "image_url": { "url": "data:image/png;base64,..." },
        },
      ],
    },
  ],
  "response_format": { "type": "json_object" }, // 可选
  "tools": [],
  "tool_choice": "auto",
  "temperature": 0.2,
}
```

- `messages[].content`:`str | list[ContentPart] | null`。
  - `ContentPart` 仅两种:`{"type":"text","text":str}`、`{"type":"image_url","image_url":{"url":str}}`;
  - 文本总长 ≤ 100000 字;图片 ≤ `MAX_IMAGES_PER_REQUEST`(默认 8);
  - 图片 URL 只接受 `data:image/(png|jpeg|webp);base64,...`(本平台只发 data URL)
    或 `http(s)://`;单图 data URL 长度 ≤ `MAX_IMAGE_DATA_CHARS`(默认 8_000_000);
  - 违反 → 400 `llm.invalid_message`。
- `response_format`:仅接受 `{"type":"json_object"}` 或
  `{"type":"json_schema","json_schema":{...}}`;其它 → 400 `llm.invalid_response_format`。
  原样透传给 litellm。

### 5.2 响应

`CompletionResponse` 增 `fallbacks: list[FallbackHop] | null`;
流式 `CompletionChunk` 的 **done 帧**同样带 `fallbacks`(中间帧不带)。

```jsonc
{ "model": "deepseek/deepseek-chat", "content": "...", "usage": {...},
  "fallbacks": [{ "from_model": "deepseek/deepseek-chat",
                  "to_model": "openai/gpt-4o-mini",
                  "code": "llm.upstream_error",
                  "message": "503 Service Unavailable" }] }
```

- `FallbackHop`:`from_model` / `to_model` / `code` / `message`(截断 500 字)。
- 没切过模型时 `fallbacks` 为 `null`(SSE 按 `exclude_none` 省略)。

### 5.3 fallback chain 语义

- 配置:`LLM_GATEWAY__FALLBACK_MODELS`(有序列表,默认空=关闭)。
- 候选链 = `[请求模型(或默认)] + FALLBACK_MODELS`(去重、去主模型)。
- **可切换**的失败:litellm 的 `RateLimitError` / `Timeout` / `APIConnectionError` /
  `InternalServerError` / `ServiceUnavailableError`,或 `APIStatusError` 的
  `status_code ∈ {408,429,500,502,503,504}`。
- **不可切换**(参数/鉴权/模型不存在等 4xx):直接 502 `llm.upstream_error`,
  `details.attempts` 记录已尝试的模型与错误。
- 全部失败 → 502 `llm.upstream_error`,`details` 带 `attempts` 与 `fallbacks`。
- 流式:**开流前**失败可换下一个候选;一旦已下发正文增量,失败只发 error 帧,不重试
  (避免重复正文)。
- 每次切换:日志 `llm_fallback`(warning)+ 指标
  `llm_fallbacks_total{service,from_provider,to_provider,code}`。

### 5.4 `/v1/models`

保持现状:返回 `default_model` 与平台可用列表;`AVAILABLE_MODELS` 为空时只返回默认模型。
orchestrator 的 `/api/v1/models` 是它的投影(带 60s 缓存与网关不可达兜底)。

## 6. orchestrator 契约变更

### 6.1 模型绑定(contextvar)

`services/llm.py` 新增进程内 contextvar(与 `usage_service` 同风格):

- `bind_model(model: str | None)` / `current_model() -> str | None`;
- `GatewayClient` 取模型的顺序:`调用参数 model` > `current_model()`(P1-G 绑定) >
  `构造函数 model(LLM_MODEL)`;仍为空则不带 `model` 字段(网关走默认)。
  绑定优先于构造配置,否则部署时填了 `LLM_MODEL` 就永远选不到别的模型。

绑定位置(请求/job 边界一处不落):`_invoke_plan`、`approve_run`、`confirm_run`、
`answer_run`、对话路径(`post_message` / `stream_message`)。

### 6.2 fallback 事件

`services/llm.py` 另加 fallback 观察者 contextvar:

- `bind_fallback_observer(observer)`;`GatewayClient` 在响应/收尾帧里看到非空
  `fallbacks` 就 `await observer(hops)`;
- run 路径由 `services/llm_binding.py::run_fallback_events(session, run)` 绑定观察者,
  落事件 **`run.llm.fallback.v1`**(`record_outbox=False`,进 `/events` 与 SSE):

```jsonc
{
  "requested_model": "deepseek/deepseek-chat", // 绑定时的模型(可为 null)
  "resolved_model": "openai/gpt-4o-mini", // 最终成功的模型
  "hops": [
    { "from_model": "...", "to_model": "...", "code": "...", "message": "..." },
  ],
}
```

- 对话路径没有 run:观察者不绑定,只记 orchestrator 日志 `llm_fallback`;
- 该事件不是过程项(item),前端事件流按普通事件渲染即可(未知类型忽略正文)。

### 6.3 API

| 端点                                                     | 变更                                                                                                                                         |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/v1/models`                                     | 新增。返回 `default_model`(可为 null)与 `models`(`[{id, owned_by}]`);数据来自网关(缓存 60s);网关不可达回退 `LLM_AVAILABLE_MODELS`(空则 `[]`) |
| `POST /api/v1/runs`                                      | `RunCreate.model: str \| null`(≤120);不在白名单 → 400 `llm.model_not_allowed`                                                                |
| `GET /api/v1/runs/{id}` / 列表                           | `RunRead.model: str \| null`                                                                                                                 |
| `POST /api/v1/conversations`                             | `ConversationCreate.model: str \| null`                                                                                                      |
| `PATCH /api/v1/conversations/{id}`                       | `ConversationUpdate.model: str \| null`                                                                                                      |
| `GET /api/v1/conversations*`                             | `ConversationRead.model: str \| null`                                                                                                        |
| `POST /api/v1/conversations/{id}/messages`(含 `/stream`) | `MessageCreate.model: str \| null`(本轮覆盖,优先于会话值;由本条消息触发的 run 记为它的 `runs.model`)                                         |

模型解析优先级(chat/run 创建):`message.model` > `conversation.model` > `None`(网关默认)。
白名单校验在 run 创建与对话模型变更两处做(可用列表为空时跳过,交由网关兜底)。

### 6.4 结构化输出

- `LLMClient.complete/stream_events/stream` 增可选 `response_format` 参数;
  `GatewayClient` 非空时放进请求体。
- 首个使用方:`services/triage.py::decide` 用 `json_schema`
  (`{"type":"object","properties":{"mode":{"enum":["chat","task"]}},"required":["mode"],"additionalProperties":false}`)
  取分流结果;解析仍保留容错(模型/提供方不支持时按原逻辑回落)。

### 6.5 本地工具参数校验

`ToolRegistry.execute`(orchestrator 进程内工具:`todo_write`/`spawn_subagent`/记忆/
技能读取/offload 回读/文档工具)在执行前用
`packages/common/common/tools.py::validate_arguments(spec.parameters, args)` 校验;
不通过时返回既有工具错误形状:`{"error": "工具参数无效: <首条错误>"}`,不执行 handler。
远端 tool-service 工具已有同等校验,不重复。

## 7. 多模态(图片输入)

- 附件侧已有:`attachments.ALLOWED_TYPES` 含 `image/png|jpeg|webp`,内容存 DB。
- 新增 `services/attachments.py::image_parts_for_message(session, tenant_id, message_id, *,
max_count, max_bytes, max_total_bytes) -> list[dict]`:取该消息绑定的图片附件,
  生成 `{"type":"image_url","image_url":{"url":"data:<mime>;base64,<...>"}}`;
  按 `created_at` 倒序累计,超过张数/单张/总量上限即停止(只带前 N 张)。
- 对话路径(`chat.prompt`):本条消息有图片时,user 消息 content 由
  `message.content + 附件文本解析` 的文本 part + 图片 part 组成(list 形状);
  历史消息里的图片也带上(`history.to_prompt(..., images_by_message=...)`,
  总数受 `LLM_IMAGE_MAX_COUNT` 约束,从最近往前取)。
- 限制(orchestrator `Settings`):`llm_image_max_count=4`、
  `llm_image_max_bytes=5_242_880`(单张原始字节)、
  `llm_image_max_total_bytes=10_485_760`。
- **边界**:任务(run)执行图本期不注入图片——run 的 goal 文本已含附件解析文本;
  任务里的图片理解留后续(需要把图片快照进 run state/checkpoint)。

## 8. 前端

1. `apps/web/src/lib/models-api.ts`:`listModels()` → `/api/v1/models`;query key `models`。
2. 对话输入区新增**模型选择器**(紧凑下拉,默认「默认模型」;`data-testid="chat-model-select"`):
   - 已有会话:切换即 `PATCH /conversations/{id}` 持久化 `model`;
   - 新会话:选择值随建会话/首条消息带上;
   - 发送消息时总带 `model`(当前选择;「默认」则不带)。
3. 会话列表/详情展示当前模型(如「deepseek-chat」小字);run 详情元信息显示 `run.model`。
4. 图片:上传/展示沿用现有附件能力;后端把图片送进模型后,用户可直接问图,
   前端无需额外改动(仅补测试覆盖图片消息渲染)。

## 9. 验证对照(验收)

| 原文验收                                      | 落地判据                                                                                                                                           |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| 主模型 503 时自动切备用且事件可见             | 网关单测:第一次调用抛 503 → 第二次用 fallback 模型成功,响应带 `fallbacks`;orchestrator 单测:run 事件里有 `run.llm.fallback.v1`(hops 正确),SSE 可见 |
| `response_format=json_schema` 返回可解析 JSON | 网关单测:透传 `response_format` 到 litellm;orchestrator 分流单测:请求带 json_schema,返回 JSON 正解析                                               |
| 上传图片能问「图里报什么错」                  | 对话单测:带图片附件的消息 → 发给模型的 user content 含 `image_url` data URL,文本 part 保留问题;张数/大小超限被截断                                 |

## 10. 测试计划(零网络)

- llm-gateway `tests/test_fallback.py`:完整/流式 fallback、不可切换错误、全失败、
  `fallbacks` 回传、指标计数;`tests/test_response_format.py`:透传与非法值 400;
  `tests/test_multimodal.py`:content 列表透传 litellm、图片张数/长度校验 400;
  `tests/test_models.py` 补白名单 400。
- agent-orchestrator `tests/test_model_routing.py`:模型 contextvar 优先级、run.model
  传递到网关请求体、对话模型解析、白名单 400、`/api/v1/models`(网关可用/不可达)、
  fallback 事件、triage `response_format`、本地工具参数校验、图片 parts 生成与上限、
  对话 prompt 多模态形状;迁移 + `alembic check`;openapi 快照重生成。
- apps/web:模型选择器(`chat-model-select`)选择→PATCH/发送带 model、
  runs-api/详情模型展示、图片消息渲染。
- 文档:`check-docs.sh` / `check-workspace.sh`。

## 11. 后续(不在本期)

- 租户级模型白名单/配额(account 套餐)、自动路由、按 run 成本归因到模型
  (已有 `run.usage.v1` 的 `by_model`,可继续细化);
- 任务路径多模态(图片进 run state/checkpoint);
- 结构化输出用于交付物 schema(交付物 JSON 校验)与工具参数纠错回路;
- 网关侧模型健康探测(熔断)与成本上限。

## 12. 实现备注(与契约的已知偏差,2026-10-28 落地时确认)

以下为落地时的等价实现或契约未覆盖的边界,后续读到实现与上文措辞不完全一致时以本节为准:

- **网关错误判定**:litellm 顶层未导出 `APIStatusError`,改为「五类异常 isinstance +
  `getattr(exc, "status_code", None) ∈ {408,429,500,502,503,504}` 鸭子判定」,
  切换语义与 §5.3 等价。
- **ContentPart 形状非法**(如 `type:"audio"`):由 pydantic 返回 422;
  §5.1 明确列出的四类违规(文本超长、图片超限、URL 协议、data URL 超长)仍是
  400 `llm.invalid_message`。
- **流式 error 帧**沿用 `StreamError{code,message}`,不带 `attempts`/`fallbacks`
  (§5.3 未定义流式错误 details);`attempts` 只出现在非流式 502 的 `details`。
- **BYOK key 复用**:一次请求只解析一次 key,fallback 到其他 provider 时沿用同一个 key;
  需要跨 provider 不同 key 时由调用方按请求拆分。
- **`GET /api/v1/models` 的 `default_model`** 取 orchestrator 自己的 `LLM_MODEL`
  (网关 `/v1/models` 的 default_model 未透传);`models` 列表来自网关(不可达回退
  `LLM_AVAILABLE_MODELS`)。
- **空模型**:请求体不带 `model` 键(而非 `"model": null`),由网关回落到
  `LLM_GATEWAY__DEFAULT_MODEL`。
- **会话模型更新**:`PATCH` 只有显式给非 null 值才更新;没有「清空回网关默认」的入口
  (传 null 视同省略)。
- **消息级模型**:`MessageCreate.model` 只作用于本轮,不落 `ChatMessage`;
  由消息触发的新 run 记为该 run 的 `runs.model` 快照。
- **本地工具预校验**(§6.5)会让原先「越界参数被工具自己夹取」的调用改为提前报错
  (如 `search_knowledge.limit=999` 返回「工具参数无效」) —— 属预期收紧。
- **子代理 run** 暂不继承父 run 的 `model`(契约未覆盖,走网关默认;需要时后续补一条
  `runs.agent_*` 之外的列或继承规则)。
- **多模态渲染前提**:对话路径的图片只随「当前消息/历史消息的附件」进入模型;
  任务(run)路径不注入图片(§7 边界)。
