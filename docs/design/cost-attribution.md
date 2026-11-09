# 成本归因与用量计价（P0-1）

> 状态：已落地（2026-11-09）。范围：llm-gateway 计价 → agent-orchestrator 透传 → account-service 落库/聚合 → Web 展示。
> 相关设计：模型出口见 [architecture.md](../architecture.md) §5，BYOK 见 [byok.md](./byok.md)，观测口径见 [langfuse-observability.md](./langfuse-observability.md)，用量的可靠投递见 [usage-delivery.md](./usage-delivery.md)。

## 1. 目标与不变量

- **成本是估算值，不是账单。** 单价来自 llm-gateway 本地 litellm 价目表，与实际供应商结算可能存在折扣/阶梯差异。
- **一次计价，全链路透传。** 只有网关算钱；编排器与账号服务只透传与聚合，不在下游重算，避免两套口径漂移。
- **NULL ≠ 0。** 未计价调用（价目表缺该模型、BYOK 自定义 model）`cost_usd` 为空，聚合时按 0 求和但计入 `unpriced_events`，前端明确提示，不把未知当免费。
- **来源与金额分离。** `billing_source` 只表示这次调用用的是平台 key 还是账号 BYOK key，不改变计价方式。

## 2. 计价（llm-gateway）

- 单源：`services/llm-gateway/llm_gateway/services/generation_trace.py` 的 `ingested_cost(model, usage) -> dict[str, float] | None`。
  - 用 `litellm.get_model_info` 的 `input_cost_per_token` / `output_cost_per_token` 算输入、输出两段；
  - 缓存按 `cache_read_input_token_cost` / `cache_creation_input_token_cost` 单独计价，缺项时回退输入价；
  - `llm_gateway/__init__.py` 设 `LITELLM_LOCAL_MODEL_COST_MAP=True`，价目表随包分发、零网络；
  - 未知模型返回 `None`（宁缺勿错价）。
- 回写：`services/llm-gateway/llm_gateway/services/completion_response.py` 的 `_bill(usage, model, *, billing_source)` 把 `cost["total"]` 写进 `UsageOut.cost_usd`，并补 `billing_source`；未知模型保持空。
- 来源判定：`api/deps.py` 的 `resolve_byok_key()` 解析到账号 BYOK key 时 `source="byok"`，否则回退平台 key 为 `"platform"`；`api/v1/completions.py` 把它传给 `complete()/stream()`。
- 对外契约：`CompletionResponse.usage` 与流式 done 片的 `usage` 都带 `cost_usd` / `billing_source`（见 `schemas/completions.py` 的 `UsageOut`）。

## 3. 透传（agent-orchestrator）

- `services/llm_gateway.py` 的 `_parse_usage()` 从网关回包读 `cost_usd` / `billing_source`，构造 `UsageSample(cost_usd, billing_source)`；脏值归一（成本转 float 失败→None，来源只认 `platform`/`byok`）。
- `services/usage.py` 的 `UsageReporter.report()` 先把样本写进编排器 outbox（`usage.report.v1`），由 dispatcher 至少一次投递 `/v1/usage/events`，body 含 `cost_usd` / `billing_source`；可靠性语义见 [usage-delivery.md](./usage-delivery.md)。
- `services/usage_events.py` 的 `run.usage.v1` 事件按模型聚合 `calls/prompt/completion/total/cost_usd`；`RunUsageTotals.cost_usd` 随 `GET /runs/{id}/usage` 与子代理用量返回。

## 4. 落库与聚合（account-service）

- 表：`account/models/account.py` 的 `UsageEvent` 增 `cost_usd Numeric(12,6) NULL`、`billing_source String(16) NOT NULL DEFAULT 'platform'`；迁移 `migrations/versions/e5b2d9c4a7f1_usage_cost.py`。
- 写入：`POST /v1/usage/events` 的 `UsageEventCreate` 接受 `cost_usd >= 0`（拒绝负成本）与 `billing_source` 枚举；`services/insights.py` 的 `record_usage()` 原样落库。
- 聚合：`GET /v1/usage/summary` 返回 `today_cost_usd` / `month_cost_usd` / `unpriced_events`，以及 `by_model[].cost_usd` / `by_model[].unpriced_events`；金额用 `Decimal` 求和后转 float，按模型仍以 tokens 降序。
- 口径：今日/本月按 UTC 自然日与自然月切分，与本文件其他用量指标一致。

## 5. 前端（apps/web）

- `lib/presentation/format.ts` 的 `formatUsd()`：1 美元以下保留 4 位小数（单次调用常为 $0.000x），否则 2 位。
- `/usage`：六张统计卡（今日/本月 tokens、请求、成本），模型用量行显示成本与「N 未计价」徽标，页面底部提示未计价调用。
- `/runs` 运行详情：用量汇总行追加成本（老 run 无字段按 0 显示）。

## 6. 边界与已知口径

- BYOK 调用仍按同一价目表估算，`byok` 只说明 key 来源；账号自供模型若不在价目表则为未计价。
- 缓存写入价仅在价目表提供时使用，否则回退输入价，属于偏保守估算。
- 不做币种换算（统一 USD）、不做预算告警、不落供应商账单对账。
- `unpriced_events` 只统计窗口内 `cost_usd IS NULL` 的事件数，不代表金额为 0。

## 7. 未做 / 后续

- ~~P0-3~~：已落地，成本随 usage 走 outbox + event_id 幂等，见 [usage-delivery.md](./usage-delivery.md)。
- `step_id` / `phase` 维度拆分（把成本归到计划步骤）未做，当前只到 run 与模型。
- 「哪次调用用了 BYOK」审计表未做；BYOK 目前只在 usage 行上标来源。
- 平台成本看板、预算告警、eval 成本归集属 P1-K。

## 8. 验证

- 网关：`npx nx test llm-gateway`（含缓存价与 byok 来源用例）。
- 编排器：`npx nx test agent-orchestrator`（`_parse_usage`、上报 body、`run.usage.v1` 聚合、OpenAPI 快照）。
- 账号：`npx nx test account-service` + 服务目录 `uv run alembic check`。
- Web：`npx nx typecheck @beauty/web`、`npx nx test @beauty/web -- --run`。
