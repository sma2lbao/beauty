# 主动告警(阈值规则 + 持久化事件)

> 状态:**已实施**(2026-11-16)。规则评估在 `agent-orchestrator` 的告警 sweeper 里,
> 告警事件落 `alerts` 表,由内部端点 `GET /internal/alerts` 读取;阈值随
> [config.py](../../services/agent-orchestrator/agent_orchestrator/core/config.py) 的
> `alerts_*` 配置项调整。
>
> 为什么不是 Prometheus Alertmanager:见 §6。

## 1. 要解决的问题

指标(`docs/observability.md` §9)只能"被看",不能"主动喊人":main 上出现过
outbox 投递失败、run 卡在 running、后台 job 连续失败,都是靠人工翻库/翻日志发现。
本模块补的是**判定 + 留痕**:固定间隔评估几条阈值规则,跨过阈值就写一条可追溯的
告警事件,同时打 warning 日志(有 Sentry DSN 时一并 `capture_message`)。

## 2. 规则

全部规则只读 `agent-orchestrator` 自己的库(零网络、零外部依赖),阈值均是配置项:

| code               | 判定                                                                | 默认阈值          | 严重度   |
| ------------------ | ------------------------------------------------------------------- | ----------------- | -------- |
| `outbox_failed`    | `outbox_events.status='failed'` 的行数                              | > 0               | critical |
| `outbox_backlog`   | `status='pending'` 且 `available_at` 早于 now − backlog 秒          | > 0(backlog 600s) | warning  |
| `run_stuck`        | `runs.status='running'` 且 `updated_at` 早于 now − stuck 秒         | > 0(stuck 1800s)  | critical |
| `run_failure_rate` | 最近 window 秒内终态 run 中 failed 占比 ≥ rate,且样本 ≥ min_samples | 0.5 / 15 分钟 / 5 | warning  |
| `job_failures`     | 最近 window 秒内 `jobs.status='failed'` 的条数                      | ≥ 3               | warning  |

- **只读**:规则不修改任何业务数据,评估失败(例如库暂不可用)只记日志并等下一轮;
- **不变式**:判定为真的规则一定被看到——每轮都从库里现算,不用缓存状态;
- **不含跨服务指标**:cost/connector/knowledge 的健康度先在各自服务留痕,
  跨服务聚合等有真正的抓取器(§6)再做。

## 3. 事件与去重

`alerts` 表是 **append-only 事件流**,一行是"某规则在某时刻进入某状态":

| 列                    | 含义                                     |
| --------------------- | ---------------------------------------- |
| `id`                  | 32 位 hex                                |
| `code`                | 规则 code(见 §2)                         |
| `severity`            | `warning` / `critical`                   |
| `status`              | `firing`(进入告警)/ `resolved`(恢复)     |
| `title` / `summary`   | 人读文案,summary 里带实际数值与阈值      |
| `value` / `threshold` | 触发时的度量值与阈值(便于画趋势、写复盘) |
| `bucket`              | 去重桶 = `epoch(now) // cooldown`        |
| `created_at`          | 事件时间(Python 侧微秒精度)              |

- 唯一约束 `(code, status, bucket)`:多副本同时评估时,同一桶内只会落一条,
  另一个副本拿到 IntegrityError 后放弃本轮写入(不影响它自己的判定);
- **只在状态切换时写入**:上一状态已是 `firing` 且本轮仍触发 → 不重复写;
  上一状态是 `firing` 且本轮不再触发 → 写一条 `resolved`;
- 当前状态 = 每个 code 的最新一行(`row_number() over partition by code`);
- `resolved` 事件保留 `alerts_retention_days`(默认 30 天),firing 事件不自动删
  (未恢复的告警本身就是待办)。

## 4. 读取面

`GET /internal/alerts`(不在 `/api/v1` 公共契约里,API 网关不代理):

```json
{
  "firing": [
    {
      "code": "outbox_failed",
      "severity": "critical",
      "title": "outbox 有投递失败的事件",
      "summary": "失败 3 条(阈值 > 0);请到 /outbox 重放或排查消费者",
      "value": 3,
      "threshold": 0,
      "since": "2026-11-16T12:00:00+00:00"
    }
  ],
  "resolved": [{ "code": "run_stuck", "...": "...", "resolved_at": "..." }]
}
```

- 与 `/internal/metrics` 同一令牌口径:`<SERVICE>__METRICS_TOKEN` 配置后必须带
  `X-Metrics-Token` 或 `Authorization: Bearer`,否则 404(不暴露端点存在性);
- `alerts_enabled=false` 时端点不注册、sweeper 不启动(默认开启,间隔 60s);
- 触发/恢复同时打 `alert firing` / `alert resolved` 的 warning/info 日志,
  `alerts_fired_total` / `alerts_resolved_total` 进 Prometheus 文本。

## 5. 配置

```sh
AGENT_ORCHESTRATOR__ALERTS_ENABLED=true
AGENT_ORCHESTRATOR__ALERTS_SWEEP_INTERVAL_SECONDS=60      # <=0 关闭 sweeper
AGENT_ORCHESTRATOR__ALERTS_COOLDOWN_SECONDS=900           # 去重桶宽度
AGENT_ORCHESTRATOR__ALERTS_OUTBOX_BACKLOG_SECONDS=600
AGENT_ORCHESTRATOR__ALERTS_RUN_STUCK_SECONDS=1800
AGENT_ORCHESTRATOR__ALERTS_FAILURE_WINDOW_SECONDS=900
AGENT_ORCHESTRATOR__ALERTS_FAILURE_MIN_SAMPLES=5
AGENT_ORCHESTRATOR__ALERTS_FAILURE_RATE=0.5
AGENT_ORCHESTRATOR__ALERTS_JOB_FAILURE_THRESHOLD=3
AGENT_ORCHESTRATOR__ALERTS_RETENTION_DAYS=30
```

## 6. 为什么不做 Prometheus 规则文件 / Alertmanager

当前部署([hybrid-deployment](./hybrid-deployment.md))没有 Prometheus、没有
Alertmanager,也没有 Loki:`/internal/metrics` 只有"被抓取时"才有意义。先加规则
文件等于加一份**没人执行**的配置。本模块把判定放进服务进程,出口是三层:
结构化日志(已有采集)、Sentry(可选 DSN,已有入口)、`alerts` 表 + 内部端点
(可被脚本/巡检读取)。

等真的上 Prometheus 时,迁移路径是:规则判定改成 `alerts` 表里的"最近状态"
指标(`alerts_firing` gauge),Alertmanager 读 gauge 做路由与抑制,本模块保留为
"判定单一真源",不重复实现通知渠道。

## 7. 测试

`services/agent-orchestrator/tests/test_alerts.py`(全部本地 sqlite,零网络):

- 规则:空库不触发;各规则的触发/不触发边界(样本量、占比、时间窗);
- sweep:首次触发写一条 firing;连续两轮不重复;条件消失写 resolved;
  同一桶内重复 sweep 不会重复落库;
- 保留期:超过 `alerts_retention_days` 的 resolved 事件被清掉,firing 不动;
- 端点:`alerts_enabled=false` 时 404、令牌口径与 `/internal/metrics` 一致、
  返回 firing/resolved 结构。
