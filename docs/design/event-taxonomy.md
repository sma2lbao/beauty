# 事件分类学:thread / turn / item(P1-D)

> 状态:**已落地(2026-10-28)**。对应 `docs/gap-2026-10/08-refresh-2026-10-27.md` 的 P1-D
> 「事件分类学(thread/turn/item,plan 一等 item)」。本文是**事件分类契约的唯一真源**:
> 事件 payload、kind/phase 语义、plan 版本化、投影与渲染规则都以本文为准。

## 1. 为什么

此前 run 的事件是**扁平流水**:`run.planned.v1` / `run.tool.called.v1` /
`run.todo.updated.v1` / `token.delta.v1` / `run.step.v1` 各自为政,没有统一
「这是哪一类过程项、处于生命周期哪一段」的分类。结果是:

- **前端无法统一渲染**:计划是消息文本、工具是审计卡片、todo 是清单、文件变更是
  diff——每类各自解析,新增一类就加一套组件;
- **plan 不是一等过程项**:计划只存在 `runs.plan_markdown` 快照 + 两条事件
  (`run.planned.v1` / `run.plan.edited.v1`),修订历史要靠快照回看,版本不可寻址;
- **无法按 item 回放/中断/续跑**:事件流能拼出正文,但拼不出「模型做过哪些过程项、
  各自从何时到何时、结果如何」。

Codex 的做法是把 plan / commandExecution / fileChange / mcpToolCall / webSearch
定义成自带 `started → delta → completed` 生命周期的 **item**;AG-UI 也有
Interrupts / State / Subagents / Reasoning 事件族。本设计把 item 分类学落到现有
事件溯源上(**零迁移**),先对齐 thread/turn/item 三级与 plan 一等 item,
AG-UI 适配层留到后续(见 §9)。

## 2. 三级模型

| 层         | 本系统对应                                                                 | 标识                                                                                        | 语义                                                     |
| ---------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| **thread** | 会话(`ChatConversation`);无会话的 run 以 `run:<root_run_id>` 自成一 thread | `thread_id`                                                                                 | 同 thread 内多 turn 共享上下文与历史                     |
| **turn**   | run(用户的一次任务请求)                                                    | `turn_id = run.id`                                                                          | retry/resume 是同 thread 的新 turn(`attempt` 区分重跑)   |
| **item**   | run 内的原子过程项(计划/工具/文件变更/MCP 调用/检索/todo)                  | `item_id = "<run_id>:<attempt>:<kind>:<n>"`;plan 用 `"<run_id>:plan:<version>"`(按版本寻址) | 只增不改,`started/completed` 两条按 `item_id` 合并成一项 |

`thread_id` 解析规则(同一份实现,`services/items.py`):

1. run 有 `conversation_id` → `thread_id = conversation_id`;
2. 否则沿 `parent_run_id` 链找到根 run → `thread_id = f"run:{root.id}"`
   (retry/子 run 与父 run 同 thread;链深上限 8 层,防环)。

`turn_id` 恒为 `run.id`(每次 run 都是独立 turn);`attempt` 记录 resume 重跑次数。

## 3. 事件契约 `run.item.v1`

一条 item 事件 = 一个过程项的某个生命周期节点。payload:

| 字段          | 类型            | 必填 | 说明                                                                            |
| ------------- | --------------- | ---- | ------------------------------------------------------------------------------- |
| `item_id`     | string          | 是   | 项标识;同项多相位共用同一 `item_id`                                             |
| `thread_id`   | string          | 是   | 见 §2                                                                           |
| `turn_id`     | string          | 是   | `run.id`                                                                        |
| `kind`        | string          | 是   | `plan` / `tool` / `file_change` / `mcp_call` / `search` / `todo`(见 §4)         |
| `phase`       | string          | 是   | `started` / `delta` / `completed` / `failed`(见 §5)                             |
| `status`      | string          | 否   | 终态细粒度:`completed` / `failed` / `denied`;`started` 不带(投影视为 `running`) |
| `node`        | string \| null  | 否   | 产出该事件的图节点(`plan`/`execute`/`tool_exec`);计划编辑、todo 快照为 `null`   |
| `step_id`     | string \| null  | 否   | 关联的 `run.step.v1` 步骤(07 S1);拿不到时为 `null`                              |
| `attempt`     | integer         | 是   | run 的尝试次数                                                                  |
| `title`       | string          | 是   | 短标题,前端直接显示(如 `写入 src/a.ts`、`检索:fastapi`、`计划(第 2 版)`)        |
| `data`        | object          | 是   | kind 专属结构化内容(见 §4);无内容时为 `{}`                                      |
| `started_at`  | string(ISO8601) | 否   | 该相位开始时间(started 相位=项开始时间;completed 相位=项开始时间,便于时长计算)  |
| `finished_at` | string(ISO8601) | 否   | 完成时间,仅 completed/failed 相位带                                             |
| `duration_ms` | integer         | 否   | 完成相位带,`finished_at - started_at` 的毫秒数                                  |

示例(工具项,两条事件):

```json
{
  "item_id": "run_abc:1:file_change:2",
  "thread_id": "conv_9f",
  "turn_id": "run_abc",
  "kind": "file_change",
  "phase": "started",
  "node": "execute",
  "step_id": "run_abc:1:execute:1",
  "attempt": 1,
  "title": "写入 src/a.ts",
  "data": { "name": "write", "arguments": "{\"path\":\"src/a.ts\"}" },
  "started_at": "2026-10-28T12:00:01.234567+00:00"
}
```

规则:

- **append-only**:item 事件写进 `run_events`(seq 单调),不改表、不加列(零迁移);
- **不进 outbox**:`record_outbox=False`,与 `token.delta.v1` / `run.step.v1` 同级,
  是展示/回放记录;对外领域事件仍由既有 `run.*.v1` 承担;
- **回放可见**:`GET /runs/{id}/events` 与 SSE **包含** `run.item.v1`(只排除
  `run.step.v1` 这类高噪声步骤事件),断线重放可重建完整过程;
- **同项合并**:同 `item_id` 的 `started` + `completed` 两相位由投影合并为一项
  (§6);`delta` 相位预留给「同项的增量输出」(命令流式输出等),当前计划正文
  仍走 `token.delta.v1`,item 在 `completed` 落全文。

## 4. kind 与 data

| kind          | 触发点                                                  | data 字段(关键)                                                                                                    | 说明                                                               |
| ------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| `plan`        | `run_execution._invoke_plan`(生成)/ `update_plan`(编辑) | `version`(第几版)、`supersedes`(被替代的 item_id)、`content`(计划全文)、`chars`                                    | **一等 item**:plan 修订=新版本,不再只有快照见 `runs.plan_markdown` |
| `tool`        | `graph/nodes.py` execute/tool_exec 的每次工具执行       | `name`、`arguments`、`ok`、`detail`、`risk`、`decision`、`tool_call_id`、`error_code`、`result_chars`、`truncated` | 普通工具(读/列/搜索/执行等)                                        |
| `file_change` | 同上;工具名 `write`/`edit`                              | 同 `tool` + `paths`、`sha256_before`、`sha256_after`                                                               | 文件变更;前端可跳 diff                                             |
| `mcp_call`    | 同上;工具名 `mcp__<slug>__<tool>`                       | `server`(slug)、`tool`、`name`、`arguments`、`ok`、`duration_ms`、`error_code`                                     | MCP 远程调用                                                       |
| `search`      | 同上;工具名 `fetch`/`search_knowledge`                  | `name`、`arguments`、`ok`、`duration_ms`                                                                           | 检索/抓取                                                          |
| `todo`        | `services/todos.py` 的 `todo_write` 写入快照时          | `todos`、`counts`、`revision`                                                                                      | 任务清单快照升级为一等 item(`run.todo.updated.v1` 仍保留)          |

分类规则集中在 `graph/items.py::classify_tool_kind(name)`(纯函数,可单测):

1. `todo_write` → `todo`;
2. `mcp__*` → `mcp_call`;
3. `write` / `edit` → `file_change`;
4. `fetch` / `search_knowledge` → `search`;
5. 其余(`read`/`glob`/`grep`/`list_dir`/`exec`/`ask_question`…)→ `tool`。

## 5. 相位语义

| phase       | 何时落                                    | 说明                                     |
| ----------- | ----------------------------------------- | ---------------------------------------- |
| `started`   | 过程项开始(计划起草、工具执行前)          | 前端显示「进行中」;带开始时间与初始数据  |
| `delta`     | 过程项进行中产生增量(当前预留,暂无发射点) | 同 `item_id` 追加;避免每 token 一条 item |
| `completed` | 过程项结束                                | 带 `status` 与结果 data、结束时间、耗时  |
| `failed`    | 过程项失败(计划起草异常;工具执行抛错)     | 带 `status=failed`;run 级失败另有事件    |

`status` 细粒度(仅终态):

- `completed`:正常完成;
- `failed`:执行失败(工具错误信封或异常);
- `denied`:未执行(审批策略禁止/确认被拒/参数非法),item 只有一条 `completed`
  事件(`status=denied`),不伪造 `started`。

## 6. 投影 `GET /api/v1/runs/{id}/items`

前端不直接拼原始事件:服务端把 `run.item.v1` 投影成「每项一条」的视图
(`services/items.py::list_items`,与 `/steps` 同为事件投影,零迁移)。

`RunItemRead` 字段:`item_id`、`thread_id`、`turn_id`、`kind`、`phase`(最新相位)、
`status`(终态细粒度或 `running`)、`node`、`step_id`、`attempt`、`title`、`seq`
(首条事件的 seq,排序锚点)、`started_at`、`finished_at`、`duration_ms`、`data`
(各相位 data 按 key 浅合并,后写覆盖前写)。

合并规则:

- 按事件 seq 升序遍历,`item_id` 首次出现决定顺序与静态字段;
- 例外:`node`/`step_id` 允许由后续相位补全——plan 的 `started` 相位在计划步骤
  结束前落库、拿不到 `step_id`,不补全会让投影永远停在 `null`;
- `phase`/`status`/`finished_at`/`duration_ms` 取最新相位;
- `data` 按 key 浅合并(旧值先、新值后),`kind` 专属字段不删;
- 无 `run.item.v1` 的 run 返回空数组(不是 404)。

## 7. 发射点(实现锚点)

| 位置                                                   | 内容                                                                                                         |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `graph/items.py`                                       | 分类学常量、`ItemSpan`、`ItemProjector`、`classify_tool_kind`(节点只上报,不落库)                             |
| `graph/steps.py`                                       | `StepSpan` 增加 `step_id`/`attempt`/`round`;`StepProjector` 在 `begin` 即分配 step_id,暴露当前步骤 id        |
| `services/run_events.py`                               | `_RunItemSink` + `_item_stream`:图调用边界把 item span 落成 `run.item.v1`(含 thread/turn/item id 与 step_id) |
| `services/run_execution.py`                            | `_invoke_plan` 落 plan `started`/`completed`(或 `failed`);`update_plan` 落新版本 `completed`                 |
| `graph/nodes.py`                                       | execute/tool_exec 每次工具调用落 `started` + `completed`(denied 只落 `completed`);节点经 `runtime.items`     |
| `services/todos.py`                                    | `todo_write` 落 `run.todo.updated.v1` 后追加 `kind=todo` 的 item                                             |
| `graph/runtime.py` / `run_execution_approvals._config` | `GraphRuntime.items` 注入(与 delta/steps 同一机制)                                                           |

## 8. 前端渲染

- 统一 item 渲染器 `apps/web/src/components/console/run-item-timeline.tsx`:
  按 `kind` 选择卡片(计划=Markdown+版本徽标、工具=参数/结果折叠、文件变更=路径与
  hash、MCP=server/tool、检索=查询、todo=清单);
- 每项**可展开/折叠**:默认折叠结果详情,展开显示 `data` 结构化字段;`started`
  项显示进行中,`failed`/`denied` 显示状态徽标;
- 计划项显示版本徽标(第 N 版),历史版本折叠在「旧版本」分组里——满足「plan
  修改落新 item 版本」;
- 数据来自 `GET /runs/{id}/items`(轮询快照,与 `/steps` 同频);SSE 仍推
  `run.item.v1` 原始事件,`useRunEvents` 不感知 item 分类即可(未知事件忽略)。

## 9. 与既有模型的边界

- **step(S1)**:item 与 step 是「过程项」与「节点步骤」两个维度,item 通过
  `step_id` 挂到 step 上;`GET /runs/{id}/steps` 契约不变,步骤时间线继续存在。
- **S2(step 级 token/成本)**:item 的 `step_id` 就是 S2 的锚点;token 流仍是
  `token.delta.v1`,后续做成本归因时按 step_id 聚合即可,不需要新增 item 事件。
- **checkpoint(P1-C)**:item 的 `step_id` 为「过程项 ↔ checkpoint」提供定位锚点,
  checkpoint 回退时可按 item 区间呈现。
- **AG-UI(后续)**:`run.item.v1` 的 kind/phase 可直接映射到 AG-UI 的 activity
  事件族;Interrupts/State/Subagents 事件族不在本期(见 08a 矩阵第 7 条)。
- **不做**:不改表、不加迁移;不把 `token.delta.v1` 改成 item(正文流保持独立);
  item 不进 outbox(展示面事件);子 run 的事件树留到 P1-E。

## 10. 验收对照

| 08-refresh P1-D 验收    | 本设计的落点                                                                   |
| ----------------------- | ------------------------------------------------------------------------------ |
| UI 能按 item 展开/折叠  | `run-item-timeline.tsx` 每项折叠结果、展开结构化 data;run 详情新增「运行项」   |
| plan 修改落新 item 版本 | `item_id="<run>:plan:<version>"` + `data.version/supersedes`;编辑产生新版本    |
| 事件可回放重建完整过程  | `run.item.v1` append-only、进 `/events` 与 SSE;`/items` 投影可还原每项生命周期 |
