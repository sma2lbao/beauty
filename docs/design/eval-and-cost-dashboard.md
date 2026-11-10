# 评测与成本看板契约（P1-K）

状态：**已落地（2026-11-10）**，落地位置与偏离见 §7；实现分三块：eval harness（agent-orchestrator 侧）、
平台成本汇总 API（account-service）、管理台成本页签（Web）。上游背景见
[../gap-2026-11/00-refresh-2026-11-09.md](../gap-2026-11/00-refresh-2026-11-09.md) §5.2 P1-K。

## 1. 目标与非目标

**目标**：补观测三角的最后两角——质量门禁与成本运营视图。

1. **eval harness**：把「黄金 run 那一类端到端断言」提升为**可枚举、可断言、可比较**的回归
   评测集；零网络、确定性、可进 CI，失败退出码非 0。
2. **成本看板**：管理面按天/按模型/按来源聚合全平台 usage_events 成本，让运营一眼看到钱
   花在哪儿、有没有未计价调用。

**非目标（本期不做，写进残余）**：真实模型质量评测（live profile 需外部服务与预算）、
LLM-as-judge 打分、自动生成测试集、成本预测/预算告警、按 run 的成本明细页、前端 eval 面板。

## 2. eval harness

### 2.1 位置与入口

- 代码在 services/agent-orchestrator/agent_orchestrator/evals/（进包，随 AO 的 lint/测试/覆盖率）：
  - fakes.py：脚本化 FakeLLM（按序吐 LLMReply）、FakeToolClient（内存工具）、零网络守卫
    （复用 golden-run 口径：AF_INET/AF_INET6 connect 即失败）、临时 sqlite 设置工厂。
  - suites.py：内置用例集（Python 数据结构）。
  - assertions.py：断言求值（返回失败原因列表）。
  - runner.py：逐用例建进程内 TestClient（create_app(settings, llm_client=..., tool_client=...,
    toolkit_factory=...)），执行步骤、收集指标、汇总结果。
  - report.py：JSON 报告 + 基线比较 + 控制台表格。
  - **main**.py：CLI。
- 包装脚本 scripts/eval/eval.sh（对齐 scripts/golden-run/golden-run.sh：优先仓库本地 uv 缓存、
  cd services/agent-orchestrator、uv run python -m agent_orchestrator.evals "$@"）。

CLI：

```sh
bash scripts/eval/eval.sh                          # 全部内置 suite，与基线比较
bash scripts/eval/eval.sh --suite task-approval    # 单个 suite
bash scripts/eval/eval.sh --json reports/eval/x.json --update-baseline services/agent-orchestrator/evals/baseline.json
bash scripts/eval/eval.sh --suite-json path/to/suite.json   # 外部用例集
```

默认基线 services/agent-orchestrator/evals/baseline.json（提交进 git）；报告默认写
reports/eval/<UTC 时间戳>.json（reports/ 已 gitignore）。

### 2.2 用例 schema

内置 suite 与外部 JSON 用同一形状（Python 侧是 dataclass）：

```json
{
  "name": "task-approval",
  "cases": [
    {
      "id": "approve-then-complete",
      "kind": "task",
      "goal": "整理本周日程并发布说明",
      "llm_script": [
        "# 执行计划\n- [ ] 检索日程",
        {
          "tool_calls": [
            { "name": "external_publish", "arguments": { "title": "周报" } }
          ]
        },
        "已发布。"
      ],
      "steps": ["create_run", "approve_plan", "confirm_all", "wait_terminal"],
      "assert": {
        "run_status": "completed",
        "events_include": [
          "run.created.v1",
          "run.planned.v1",
          "run.confirmation.received.v1",
          "run.completed.v1"
        ],
        "events_exclude": ["run.failed.v1"],
        "tool_calls": ["external_publish"],
        "min_events": 5
      }
    }
  ]
}
```

- kind：chat（流式对话直答）或 task（建 run → 计划审批 → 确认 → 终态）。
- llm_script：按调用次序的模型回复；字符串 = 正文，对象 {"tool_calls":[...]} = 工具调用请求
  （arguments 可为对象或 JSON 字符串，统一转 JSON 字符串）。脚本不足即失败（与 golden-run 同口径）。
- steps：默认按 kind 推导；显式列出用于跳过/重复审批。confirm_all = 循环确认直到无 pending。
- assert 支持的键（全部可选，逐条给中文失败原因）：
  - run_status：pending|planning|awaiting_approval|running|blocked|completed|failed|cancelled
  - events_include / events_exclude：事件类型集合断言
  - tool_calls：按名称集合断言（子集匹配，出现即可）
  - reply_contains：对话正文包含子串
  - min_events / max_events
  - deliverables：交付物数量（非负数）
  - max_total_tokens：本次用例消耗 token 上限

内置三个 suite（覆盖主要路径）：

1. chat-direct：流式直答不建 run、正文完整、无 run.created.v1。
2. task-approval：建 run → 计划审批 → 危险工具确认 → 交付 → run.completed.v1。
3. tool-policy：策略表命中 auto 的工具不经确认直接执行（断言无 run.confirmation.required.v1）。

### 2.3 报告与基线

JSON 报告（契约）：

```json
{
  "version": 1,
  "generated_at": "2026-11-10T14:00:00+00:00",
  "suites": [
    {
      "name": "task-approval",
      "cases": [
        {
          "id": "approve-then-complete",
          "ok": true,
          "duration_ms": 420,
          "run_status": "completed",
          "events": ["run.created.v1"],
          "tool_calls": ["external_publish"],
          "tokens": 123,
          "deliverables": 1,
          "failures": []
        }
      ]
    }
  ],
  "totals": { "cases": 3, "passed": 3, "failed": 0, "tokens": 300 }
}
```

- 基线文件 = 同一 schema（去掉 generated_at 的抖动量）；--update-baseline 写当前结果。
- 比较规则：用例新增/删除、ok 由 true→false、tokens 超基线 +20%、events 集合增减、
  tool_calls 集合增减 → 均记 **drift**（打印并计入退出码）。
- 退出码：0 = 全通过与无 drift；1 = 有用例失败；2 = 仅 drift。
- --max-tokens、--max-total-tokens 可额外设阈值（缺省只比基线）。

### 2.4 约束

- **零网络**：进程内 TestClient，--disable-socket 同口径守卫，模型/工具/知识库全 fake；
  不连 llm-gateway、不连 tool-service。
- 每个用例独立临时 sqlite（跑完删除），互不污染；进程级状态（token 预算、模型缓存）每用例复位。
- 不写 docs/、不改运行时代码；只新增 evals 包 + 脚本 + 测试 + 基线 + AO README 段落。

## 3. 平台成本汇总 API（account-service）

### 3.1 端点

GET /v1/admin/cost/summary?days=30（网关 /api/account/v1/admin/cost/summary）：

- days：整数 1..90，默认 30。
- 鉴权：require_admin（与既有管理面一致）；**读端点不写 audit_logs**。
- 时间口径：UTC 自然日；窗口 = 含今天在内最近 days 天（从今天 00:00 UTC 回退 days-1 天）。

### 3.2 响应

```json
{
  "days": 30,
  "since": "2026-10-12T00:00:00Z",
  "totals": {
    "events": 120,
    "tokens": 45000,
    "cost_usd": 1.23,
    "unpriced_events": 4,
    "platform_events": 100,
    "byok_events": 20
  },
  "cost_by_source": [
    { "source": "platform", "events": 100, "cost_usd": 1.1 },
    { "source": "byok", "events": 20, "cost_usd": 0.13 }
  ],
  "by_day": [
    {
      "date": "2026-10-12",
      "events": 0,
      "tokens": 0,
      "cost_usd": 0.0,
      "unpriced_events": 0
    }
  ],
  "by_model": [
    {
      "model": "gpt-x",
      "events": 80,
      "tokens": 30000,
      "cost_usd": 0.9,
      "unpriced_events": 0
    }
  ],
  "top_accounts": [
    {
      "account_id": "…",
      "email": "a@b.c",
      "events": 40,
      "tokens": 12000,
      "cost_usd": 0.5
    }
  ]
}
```

- by_day：**按日期升序、补齐窗口内每一天**（没有事件补 0），前端直接画。
- by_model：按 cost_usd 降序取前 20。
- top_accounts：按 cost_usd 降序取前 10，email 来自 users（账号已删/查不到给 null）。
- cost_usd 为浮点（与既有 UsageSummary 同口径），未计价事件不计入成本、单独计数。

### 3.3 实现要点

- 聚合走 SQL（GROUP BY）：按天用 func.date(UsageEvent.created_at)（sqlite 返回字符串、
  PG 返回 date，统一 str()[:10]）；跨账号查询（管理面例外于账号隔离），仓储方法放
  account/repositories.py（如 AdminUsageRepository.cost_by_day/cost_by_model/cost_by_source/
  cost_by_account）。
- 服务层组装 + 补零天 + 取 topN；schema 放 account/schemas/admin.py。
- 测试：sqlite 直插 usage_events（含 BYOK/未计价/null 成本/跨天跨账号），断言补零、排序、
  topN 截断、days 边界（1/90/0→422/91→422）、非管理员 403 admin.required。
- 不新增迁移（usage_events 已有 cost_usd/billing_source）。

## 4. Web 管理台成本页签

- 位置：/admin 新增第三个页签「成本」（features/admin/admin-cost-panel.tsx +
  use-admin-cost-model.ts；pages/admin.tsx 的 TABS 加一项；AdminTab 扩联合类型）。
- 数据：lib/admin-api.ts 加 costSummary(days) + 类型；query-keys.ts 加
  adminCostSummary(days)；days 切换 7/30/90，默认 30。
- 布局（复用 @/components/console/ui 与 @/lib/presentation 的 formatUsd，不自造图表库）：
  1. 范围切换（7/30/90 天）。
  2. 统计卡：总成本、调用次数、tokens、未计价条数（ToneBadge 提示）。
  3. 来源拆分：platform / byok 两行（成本 + 占比条）。
  4. 按天成本：CSS 条形图（高度按窗口内最大成本归一，title 给日期与金额），无数据空态。
  5. 按模型表：模型 / 调用 / tokens / 成本 / 未计价。
  6. Top 账号表：邮箱（缺失显示账号 id 前缀）/ 调用 / tokens / 成本。
- 状态：loading 骨架、forbidden（403）沿用现有 AdminAccessPanel 口径、错误就地中文提示、
  空数据空态；源文件 ≤400 行（超了拆组件）。

## 5. 验证口径

- eval：bash scripts/eval/eval.sh 退出码 0；AO 全量 npx nx test agent-orchestrator
  覆盖率仍 ≥82；npx nx lint agent-orchestrator 绿；新增 tests/test_evals.py 覆盖
  断言失败、脚本不足、基线 drift、JSON 报告形状、零网络守卫。
- 成本 API：npx nx test account-service（覆盖率门槛 77）+ lint；无迁移（alembic check 不动）。
- Web：npx nx lint @beauty/web、npx nx typecheck @beauty/web、
  npx nx test @beauty/web -- --run；新增面板与 model 测试。
- 全仓：bash scripts/check-docs.sh、bash scripts/check-workspace.sh、git diff --check。

## 6. 残余（预期）

- 无 live 模型评测：真实质量门禁仍需接模型与预算，且结论受模型版本漂移影响。
- 成本看板只读 usage_events（无请求级/工具级成本下钻）；usage_events 无账号外键，
  top_accounts 的 email 查询是「能查到就显示」。
- 无预算告警/配额联动；无成本预测。
- eval 基线需人工 --update-baseline 维护，仓库不自动跑（CI 挂接为后续项）。

## 7. 落地位置与偏离（2026-11-10）

### 7.1 eval harness（agent-orchestrator）

`agent_orchestrator/evals/`：`__init__.py`、`suites.py`（套件加载与用例校验）、`runner.py`（每用例驱动）、`fakes.py`（`ScriptedLLM`/`FakeKnowledge`/`FakeDangerTool`/`NetworkGuard`）、`assertions.py`（断言求值）、`report.py`（JSON 报告 + 基线漂移）、`__main__.py`（CLI 入口）；`scripts/eval/eval.sh`；默认基线 `services/agent-orchestrator/evals/baseline.json`；`tests/test_evals.py`（23 例）；`services/agent-orchestrator/README.md` 新增「评测 harness」小节。

行为要点：`runner.py` 以 `TestClient` 驱动真实 app（注入 `ScriptedLLM`、FakeKnowledge、含 `FakeDangerTool` 的 toolkit 与 verifier），每用例独立 `TemporaryDirectory` + 独立 sqlite；`NetworkGuard` 拦截 `AF_INET`/`AF_INET6` 的 `connect`（非 loopback 即失败），保证零网络；`ScriptedLLM` 按 4 字符≈1 token 估算并经 `usage_service.record` 记用量；报告默认写 `reports/eval/<UTC 时间戳>.json`，只有 `--update-baseline` 才覆盖 `evals/baseline.json`。

偏离（均经裁决认可）：① `Case` 增可选 `policies` 字段（tool-policy 套件先 `PUT /v1/approval-policies`）；② task / tool-policy 用例的 `llm_script` 需 4 条（`deliver` 节点必调 LLM）；③ 用 `TestClient` 同进程驱动而非 mock 内部服务；④ `--suite` 单套只比对所选套件；⑤ token 为估算值；⑥ 未知套件/缺失 suite 文件退出码 1。

### 7.2 成本汇总 API（account-service）

`account/repositories.py` 的 `AdminUsageRepository` 新增 `cost_totals`/`cost_by_day`/`cost_by_model`/`cost_by_source`/`cost_by_account`（跨账号 GROUP BY，`created_at` 统一 `str()[:10]`）、`UserRepository.emails_for`；`account/services/admin.py::cost_summary`；`account/schemas/admin.py` 的 `CostTotals`/`CostSourceRow`/`CostDayRow`/`CostModelRow`/`CostAccountRow`/`CostSummary`；`account/api/v1/admin.py` 的 `GET /v1/admin/cost/summary?days=`；`tests/test_admin_cost.py`（6 例）。无迁移（`usage_events` 已有列）。

语义：UTC 自然日窗口含今天（`since = 今天 - (days-1)`），`by_day` 补零并升序，`by_model` top20、`top_accounts` top10（成本降序、并列按 tokens 再按 id），`unpriced_events` 单独计数、金额计 0；读端点不写 `audit_logs`。

### 7.3 Web 成本页签（apps/web）

新增 `features/admin/use-admin-cost-model.ts`、`features/admin/admin-cost-panel.tsx`、`features/admin/admin-access-panel.tsx`（抽出 `AdminAccessNotice` 供页面级复用）+ 两个测试文件；修改 `lib/admin-api.ts`（`costSummary(days)` + 类型）、`lib/query-keys.ts`（`adminCostSummary(days)`）、`features/admin/use-admin-model.ts`（`AdminTab` 加 `cost`）、`pages/admin.tsx`（第三页签）。页签未激活不发请求；7/30/90 默认 30。

### 7.4 验证结果

- `bash scripts/eval/eval.sh` 退出码 0：3 套件 PASS（chat-direct 66 tokens；task-approval 15 事件/1 交付物；tool-policy 12 事件/1 交付物），无 drift，报告 `reports/eval/20261110T145333Z.json`。
- agent-orchestrator：全量 1162 passed（1139 + 23），覆盖率 88.11%（阈值 82），lint 绿。
- account-service：102 passed（96 + 6），覆盖率 79.10%（阈值 77），lint 绿，`alembic check` clean。
- Web：lint 0 error（6 条既有 warning）、`typecheck` 通过、98 files / 733 tests 全绿。
- 全仓：`bash scripts/check-docs.sh`、`bash scripts/check-workspace.sh`、`git diff --check`。

### 7.5 残余

- eval 未接 CI（需 `--update-baseline` 人工维护）、token 为估算、`TestClient` 同进程不覆盖真实网络与多进程；无 live 模型评测。
- 成本聚合读 `usage_events` 全表（无 `created_at` 索引）、topN 在服务层排序、无缓存；无请求级/工具级成本下钻、无预算告警/预测。
- 按天条形为纯 `div`（不引图表库）；`top_accounts` 的 email 查不到时回退账号 id 前缀。
