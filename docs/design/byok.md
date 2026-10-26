# BYOK（租户自带模型密钥）设计

> 状态：已落地（2026-10-26，06 P0-6 收口） · 单一真源
> 关联：[architecture.md](../architecture.md) §3 服务职责、§7 认证与计费、§13 风险；[gap-2026-10/06-refresh-2026-10-25.md](../gap-2026-10/06-refresh-2026-10-25.md) P0-6；[local-dev.md](../local-dev.md) §4.1；[observability.md](../observability.md) §5；服务侧操作说明见 [account-service README](../../services/account-service/README.md) 与 [llm-gateway README](../../services/llm-gateway/README.md)
> 定位：本文是「BYOK（Bring Your Own Key，租户自带模型密钥）」的单一真源——记录信任边界、接口契约、解析与回退语义、KMS/轮换口径与限制；架构层面的服务边界以 [architecture.md](../architecture.md) 为准，未完成事项以 [roadmap.md](../roadmap.md) 与 gap 册为准。

## 1. 摘要

一句话：租户在设置页填自己的 provider key，account-service 用**信封加密 + 可切换 KMS**（local / Vault Transit）落库；llm-gateway 在每次模型/嵌入请求时，按 `X-Tenant-Id` + `X-Internal-Secret` 向 account 的内部端点换取明文并短时缓存；**拿不到就回退平台 `settings.api_key`**，BYOK 故障不影响出模型。

现状（2026-10-26）：P0-6「BYOK 存而不用」已收口——调用链已打通，落点如下。

| 能力                                      | 实现位置                                                                              | 状态          |
| ----------------------------------------- | ------------------------------------------------------------------------------------- | ------------- |
| 加密存储 / 掩码列表 / 删除                | `services/account-service/account/services/insights.py`、`account/api/v1/insights.py` | ✅            |
| 信封 v2 + KMS 抽象（local / vault）       | `services/account-service/account/core/byok_kms.py`                                   | ✅            |
| 内部明文解析端点（服务间）                | `GET /v1/byok/{provider}/secret`                                                      | ✅            |
| 批量重加密端点                            | `POST /v1/byok/rotate`                                                                | ✅            |
| 网关按租户解析 + 缓存 + 回退              | `services/llm-gateway/llm_gateway/services/byok.py`                                   | ✅            |
| completion / stream / embeddings 三路接入 | `llm_gateway/api/v1/completions.py`、`embeddings.py`                                  | ✅            |
| 调用方透传租户与内部密钥                  | orchestrator `services/llm.py`、knowledge `services/embeddings.py`                    | ✅            |
| 日志脱敏与指标                            | `llm_gateway/services/byok.py`、`metrics.py`                                          | ✅            |
| 独立审计表（谁在何时用了哪把 key）        | —                                                                                     | ❌ 见 §12     |
| 成本归因区分 byok / platform 来源         | —                                                                                     | ❌ 属 06 P0-1 |

### 1.1 关键决策（D1–D10）

| #   | 决策                                                                                         | 理由 / 依据                                                                                                                                                  |
| --- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| D1  | **调用时解析**（lazy resolve），不做「配置下发 / 事件同步 / 进程内密钥表」                   | 密钥是低频变更、高频读取；下发要处理多实例一致性、失效广播与内存驻留，收益远小于一次内部 GET + TTL 缓存                                                      |
| D2  | 明文**只在内部 HTTP 上短暂存在**，account 是唯一持有密文的服务；网关不共享数据库             | 密文与解密材料同处一地（account + KMS），网关只做消费者；共享库会把加密边界摊到两个服务                                                                      |
| D3  | 内部端点用 **`X-Internal-Secret` + `X-Tenant-Id`** 双头；用户端点走 JWT                      | 密钥必须按租户取、且不能让任意内网进程拿到；`common.security.internal_secret` 统一常量时间比较（`packages/common/common/security/internal_secret.py:19-46`） |
| D4  | 解析失败（未配置 / 404 / 超时 / 5xx / 坏响应）**一律回退平台 key**，不把 BYOK 故障升级成 5xx | BYOK 是增强不是前置条件（`llm-gateway/README.md` §BYOK）；自带 key 的用户配错时仍应能对话                                                                    |
| D5  | 网关侧 **TTL 缓存 300s + 负缓存 30s + LRU 1024 + 单飞锁**                                    | 每次调用都打 account 会把内网 RT 叠加到所有模型请求上；负缓存避免「没配 key」的租户每请求都穿透；单飞锁防缓存击穿                                            |
| D6  | 密文用**信封 v2** `{"v":2,"kms","kid","ct"}`，KMS 通过 `BYOKKms` Protocol 可插拔             | 记录「哪把 KEK 加密」才能在轮换期同时解开新旧密文；Protocol 让测试注入内存 KMS、生产换 Vault 不改业务代码                                                    |
| D7  | 迁移走**懒轮换 + 批量轮换端点**（读取时顺手 rewrap，失败不阻断）                             | 存量密文不必停机迁移；批量端点给运维一次收敛机会，逐条容错（`{rotated,failed}`）                                                                             |
| D8  | 租户 key **只覆盖 `api_key`，不覆盖 `api_base`**                                             | 允许租户改 base URL 等于把平台流量导到任意地址（SSRF/数据外流），且与「唯一模型出口」的架构约束冲突                                                          |
| D9  | 明文不进日志/事件/前端；观测只落租户、provider、`key_hint`、`source`、`kid`                  | 与 [observability.md](../observability.md) §5 的禁落清单一致；`key_hint` 只有末 4 位，用于「是不是我这把 key」的排查                                         |
| D10 | 06 P0-6 选择「**接入**」而不是「明确下线」                                                   | 存储与 UI 早已存在，下线要删表删端点；接入成本是「一个内部 GET + 一个消费点」，且打通了后续成本归因（P0-1）的按来源记账前提                                  |

### 1.2 范围（做了什么 / 没做什么）

| 维度 | 已做                                                | 未做（见 §11、§12）                                        |
| ---- | --------------------------------------------------- | ---------------------------------------------------------- |
| 存储 | 信封 v2、local/vault KMS、历史裸 token 兼容、懒轮换 | 独立审计表、密钥有效性校验（保存时不试打）                 |
| 消费 | completion、stream、embeddings 三路按租户取用       | 按租户的速率隔离 / 并发闸（属 06 其他项）                  |
| 运维 | rotate 端点、指标、结构化日志                       | compose 已注入 `ACCOUNT_BASE_URL`（见 §8.3）               |
| 计费 | —                                                   | `usage_events` 不记来源，BYOK 与平台额度暂无法在账单上区分 |

## 2. 架构与信任边界

```text
浏览器 ──（JWT/Cookie）──> BFF :8007 ──> account-service :8001
                                   PUT/GET/DELETE /v1/byok（只回掩码）
                                         │
                                   byok_keys（密文，信封 v2）
                                         │  encrypt/decrypt
                                         ▼
                              BYOKKms：LocalByokKms | VaultByokKms
                                         ▲
                                         │ GET /v1/byok/{provider}/secret
                                         │ X-Internal-Secret + X-Tenant-Id
                                         │ （明文，Cache-Control: no-store）
agent-orchestrator :8002 ──┐             │
knowledge-service :8006 ──┤──> llm-gateway :8003 ──> provider（DeepSeek/…）
                           │      │ TTL 缓存 (tenant, provider)
                           │      └─ 取不到 → settings.api_key（平台 key）
                           └─ 出站钩子注入 X-Tenant-Id + X-Internal-Secret
```

**明文生命周期**（这是本设计的核心约束）：

1. account 从数据库读密文 → KMS 解密 → 明文只在该请求的内存与响应体中存在；
2. 响应体走内网 HTTP，带 `Cache-Control: no-store`，网关侧不落盘、不写日志；
3. 网关把明文放进 `ResolvedKey`（`api_key` 字段 `repr=False`，避免被调试打印带出）与 LRU 缓存，TTL 到期或淘汰即释放；
4. 明文被塞进对 provider 的请求头后，网关不再保留副本（litellm 调用结束即出作用域）。

**信任边界**：

| 区域                                                     | 谁可信                     | 说明                                                                                                  |
| -------------------------------------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------- |
| 用户端点 `/v1/byok*`（GET/PUT/DELETE）                   | 已登录租户                 | JWT 租户 claim 决定读写哪一行；响应只有掩码                                                           |
| 内部端点 `/v1/byok/{provider}/secret`、`/v1/byok/rotate` | 持有内部共享密钥的内网服务 | **留空即关闭**（`common.security.internal_secret` 未配置时一律 401）；租户由调用方 `X-Tenant-Id` 指定 |
| 网关模型端点 `/v1/completions*`、`/v1/embeddings`        | 同上（内部密钥）           | 租户头**可选**：缺失即不做 BYOK 查询，用平台 key                                                      |
| 平台管理员 / 日志系统                                    | 不可见明文                 | 日志只有 `key_hint`；但平台管理员能读数据库 + KMS 配置，仍可解出明文（见 §9）                         |

## 3. 端到端时序

### 3.1 写入 / 列表 / 删除

```text
用户 → BFF → account PUT /v1/byok/{provider} {api_key}
        provider 归一（strip + lower，空或 >32 字符 → 400 byok.invalid_provider）
        kms.encrypt(api_key) → 信封 v2 → 存 byok_keys（key_hint = 末 4 位）
        ← 200 {provider, masked_key:"••••"+hint, key_hint, updated_at}
```

### 3.2 网关解析（缓存未命中）

```text
orchestrator/knowledge → llm-gateway POST /v1/completions（X-Tenant-Id, X-Internal-Secret）
  gateway: provider = model 前缀（无 "/" → "" → 不查 BYOK）
           resolver.resolve(tenant, provider)
             命中缓存? 是 → status=cache_hit，直接用（move_to_end）
             否 → 单飞锁内二次检查 → GET account/v1/byok/{provider}/secret
                    200 → ResolvedKey(source="byok") + 正缓存 300s，status=byok
                    404 → 负缓存 30s，status=negative，回退平台 key
                    其他/超时/坏 JSON → status=error（不缓存），回退平台 key
  provider 调用结束 → usage 照常记账（不区分来源）
```

SSE 流式路径在**返回流之前**完成解析——回退决策一次性生效，不会「流到一半换 key」。

### 3.3 轮换

```text
运维改配置（BYOK_KEK_ID + BYOK_SECRET，旧密钥挪进 BYOK_PREVIOUS_SECRETS）→ 重启 account
运维逐租户 POST /v1/byok/rotate（内部头）→ 逐条 rewrap 到当前 kid
  ← {rotated, failed}；failed>0 时看 byok.rotate_failed 日志定位 provider
确认 failed=0 → 删除 BYOK_PREVIOUS_SECRETS → 网关缓存在 TTL（≤300s）内自然过期
```

## 4. 接口契约

### 4.1 account-service 端点

| 方法     | 路径                         | 鉴权     | 请求 / 响应                                                                   | 错误                                                                    |
| -------- | ---------------------------- | -------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `GET`    | `/v1/byok`                   | JWT      | `list[BYOKRead]`（按 provider 排序，只有掩码）                                | —                                                                       |
| `PUT`    | `/v1/byok/{provider}`        | JWT      | `{api_key}`（8–4096 字符）→ `BYOKRead`                                        | `400 byok.invalid_provider`、`422`（pydantic 长度）                     |
| `DELETE` | `/v1/byok/{provider}`        | JWT      | 204                                                                           | `400 byok.not_found`（不存在时）                                        |
| `GET`    | `/v1/byok/{provider}/secret` | 内部双头 | `BYOKSecretRead{provider,api_key,key_hint,key_id}`，`Cache-Control: no-store` | `404 byok.not_found`、`500 byok.unreadable`、`502 byok.kms_unavailable` |
| `POST`   | `/v1/byok/rotate`            | 内部双头 | `{rotated,failed}`                                                            | 逐条容错不抛错；配置缺失在启动期就失败                                  |

`BYOKRead` 只含 `provider / masked_key / key_hint / updated_at`——**明文永不出现在用户面响应**。内部端点缺失 `X-Tenant-Id` 时返回 `401 auth.tenant_missing`。

### 4.2 网关消费契约

| 维度          | 约定                                                                                                                              | 依据                                                 |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 内部密钥头    | `X-Internal-Secret`，与 account / orchestrator / knowledge 同源                                                                   | `common.security.SECRET_HEADER`                      |
| 租户头        | `X-Tenant-Id`，**可选**；缺失 = 不做 BYOK 查询                                                                                    | `TENANT_HEADER`；对话直答等无租户场景也能跑          |
| provider 来源 | completions: `model` 的 `"<provider>/<model>"` 前缀；无 `/` → 空串（不猜）；embeddings: `model` 或 `default_embedding_model` 前缀 | `llm_gateway/api/v1/completions.py`、`embeddings.py` |
| 解析时机      | 进入 provider 调用前；流式在返回 SSE 之前                                                                                         | 避免半路换 key                                       |
| 明文覆盖范围  | 只覆盖 `api_key`；`api_base` / 超时 / 重试仍用平台配置                                                                            | 见 D8                                                |
| 失败语义      | 返回 `None` → 调用方用 `settings.api_key`，**不是 5xx**                                                                           | D4                                                   |

受内部密钥保护的端点：`POST /v1/completions`、`POST /v1/completions/stream`、`POST /v1/embeddings`；`GET /v1/models` 仍公开（不解析 BYOK）。

### 4.3 回退矩阵（网关视角）

| 场景                                          | 结果                   | 指标 `status` | 日志                              |
| --------------------------------------------- | ---------------------- | ------------- | --------------------------------- |
| `BYOK_ENABLED=false` 或内部密钥为空           | 平台 key               | `disabled`    | 无                                |
| 无 `X-Tenant-Id` 或无 provider                | 平台 key               | `disabled`    | 无                                |
| 租户未配置该 provider（404）                  | 平台 key（负缓存 30s） | `negative`    | 无（负缓存是正常态）              |
| 正缓存命中                                    | 租户 key               | `cache_hit`   | `byok_resolved`（source=byok）    |
| 解析成功                                      | 租户 key               | `byok`        | `byok_resolved`                   |
| account 超时 / 5xx / 坏 JSON / 空 key         | 平台 key（不缓存）     | `error`       | `byok_lookup_failed`（含 reason） |
| 密文解不开 / KMS 不可用（account 侧 500/502） | 平台 key（不缓存）     | `error`       | 同上（含 status 与响应片段）      |

「不缓存 error」是有意的：account 抖一下不该让租户在 30s 内持续掉回平台额度。

## 5. 网关侧缓存实现

- 结构：`OrderedDict[(tenant_id, provider)] → (ResolvedKey | None, expires_at)`，用 `_MISS` 哨兵区分「未命中」与「负缓存（值为 None）」；
- 淘汰：超过 `BYOK_CACHE_MAX_ENTRIES`（默认 1024）时 `popitem(last=False)` LRU；
- 击穿：未命中路径在**单把 `asyncio.Lock`** 内二次检查，同一 (tenant, provider) 并发只穿透一次；
- TTL：正缓存 `BYOK_CACHE_TTL_SECONDS`（默认 300s，**也是轮换/删除的生效延迟上限**）；负缓存 `BYOK_NEGATIVE_CACHE_TTL_SECONDS`（默认 30s）；
- 语义：缓存键是 (租户, provider)，不含请求，因此不会串租户；删除/更换 key 后最多 300s 才生效——这是可接受的运维窗口，不是缺陷（需要立即生效就重启网关或调小 TTL）。

## 6. 密钥存储：信封 v2 与 KMS

### 6.1 信封格式

```json
{ "v": 2, "kms": "local", "kid": "local-1", "ct": "<密文>" }
```

- `v`：信封版本，仅识别 `2`；
- `kms`：产生密文的 KMS 类型（`local` / `vault`），用于运维排障与迁移提示；
- `kid`：KEK 标识。local 模式下即 `BYOK_KEK_ID`；vault 模式下是 Transit key 的版本无关名；
- `ct`：密文本体（local 为 SecretBox/Fernet token；vault 为 `vault:vN:…`）。

**历史兼容**：不以 `{` 开头、JSON 坏、`v!=2`、`ct` 为空 → 一律视为 v2 之前的**裸 Fernet token**，按「当前 kid → 历史 kid」顺序试解。这让升级不需要数据迁移，也保证回滚到旧版本时历史密文仍可解（见 §10）。

### 6.2 `LocalByokKms`（默认）

- 材料：`BYOK_SECRET`（当前 kid）+ `BYOK_PREVIOUS_SECRETS`（JSON：旧 kid → 旧密钥）；
- 当前 kid 与历史 kid 同名时，**当前覆盖历史**；
- 裸 token 按 kid 顺序逐个试；信封按 `kid` 精确取 box，未知 kid 立即 `byok.unreadable`（不静默回退，避免「以为解开了」）；
- `needs_rotation` = 非信封，或 `kid != 当前 kid`；`rotate` = `decrypt` 后 `encrypt`；
- 局限：KEK 与密文同库，**防的是数据库备份/误导出泄漏，不防拿到整机的攻击者**。

### 6.3 `VaultByokKms`（生产推荐）

- 调 Transit 引擎：`POST {url}/v1/{mount}/{encrypt|decrypt|rewrap}/{key}`，头 `X-Vault-Token`；
- 明文在请求体里是 base64；响应取 `data.ciphertext`；轮换用 `rewrap`（不必回传明文）；
- 超时 `BYOK_VAULT_TIMEOUT_SECONDS`（默认 5s）；`httpx.HTTPError`、非 JSON、字段缺失 → `502 byok.kms_unavailable`；
- **不能解裸 token**：从 local 切 vault 前必须先让存量密文变成 vault 信封（或重录 key），否则 `byok.unreadable`；
- 密钥材料留在 Vault，account 只持有 token——这是本设计里唯一「密文与 KEK 分离」的形态。

### 6.4 选择建议

| 场景                    | 选择                        | 说明                                                  |
| ----------------------- | --------------------------- | ----------------------------------------------------- |
| 本地开发 / 单机演示     | `local` + 默认 dev 密钥     | 代码默认；**默认值必须换**（§8.1）                    |
| 小规模自托管、无 Vault  | `local` + 随机长密钥 + 轮换 | 接受「整机失守即明文」的残余风险                      |
| 生产 / 合规要求密钥托管 | `vault`                     | 需要先解决裸 token 迁移；Vault 自身要做 HA 与令牌管理 |

### 6.5 轮换 runbook

1. 生成新密钥材料，确定新 `kid`（如 `local-2`）；
2. 把当前 `BYOK_SECRET` 以旧 kid 为 key 写进 `BYOK_PREVIOUS_SECRETS`；
3. 设新的 `BYOK_KEK_ID` + `BYOK_SECRET`，重启 account（旧密文仍可解）；
4. 逐租户调 `POST /v1/byok/rotate`（内部头）→ 确认 `failed=0`；失败条看 `byok.rotate_failed` 的 provider 定位；
5. 观察 `llm_byok_lookups_total{status="error"}` 无抬头后，删除 `BYOK_PREVIOUS_SECRETS` 再重启；
6. 网关侧无需动作：缓存 TTL 内自然切换。

补充：读取路径自带**懒轮换**——任何一次 `GET /secret` 发现 `needs_rotation` 就顺手 rewrap，失败只记 warning 并仍返回明文（下次再试）。所以即使没跑第 4 步，存量也会随访问逐步收敛；批量端点是为了「运维一次确认」而不是必需。

## 7. 脱敏、日志与可观测

允许落日志（结构化）：

| 事件                 | 字段                                                   | 用途                             |
| -------------------- | ------------------------------------------------------ | -------------------------------- |
| `byok_resolved`      | `tenant_id` / `provider` / `key_hint` / `source`       | 证明「这次请求用的是谁家的 key」 |
| `byok.rotated`       | `tenant_id` / `key_id` / `rotated`                     | 轮换收口证据                     |
| `byok.rotate_failed` | `tenant_id` / `provider` / `key_id` / `code`           | 定位不可解/不可重加密的条目      |
| `byok_lookup_failed` | `reason`（异常类名）/ `status` / 响应片段（≤200 字符） | 排查 account 不可达/契约漂移     |

禁止落日志（与 [observability.md](../observability.md) §5 一致）：`api_key` 明文、密文、`X-Internal-Secret`、完整响应体。`ResolvedKey.api_key` 用 `field(repr=False)`，避免对象被 `repr` 打出来。

指标：`llm_byok_lookups_total{service,status}`，`status ∈ disabled / negative / error / byok / cache_hit`。建议告警口径：

- `error` 持续抬头 → account 不可达或契约漂移（先看 `byok_lookup_failed`）；
- `cache_hit` 占比骤降 → 缓存被清/键空间异常；
- `byok` 占比骤降而 `negative` 抬头 → 租户 key 被删或 provider 名对不上。

**审计缺口**：当前没有「哪个租户的哪次调用用了 BYOK」的持久审计表，只有日志与指标。若后续要计费/合规，需要在网关落独立审计表（见 §12）。

## 8. 配置

### 8.1 account-service（`ACCOUNT__` 前缀）

| 变量                                  | 默认                        | 说明                                              |
| ------------------------------------- | --------------------------- | ------------------------------------------------- |
| `ACCOUNT__BYOK_KMS`                   | `local`                     | `local` / `vault`；未知取值**启动即失败**         |
| `ACCOUNT__BYOK_KEK_ID`                | `local-1`                   | 当前 KEK 标识，写进信封 `kid`                     |
| `ACCOUNT__BYOK_SECRET`                | `dev-byok-secret-change-me` | local 当前 kid 的密钥材料；**生产必须换随机长串** |
| `ACCOUNT__BYOK_PREVIOUS_SECRETS`      | `{}`                        | 历史 kid → 旧密钥的 JSON 映射（轮换期用）         |
| `ACCOUNT__BYOK_VAULT_URL`             | 空                          | Vault 地址；`vault` 模式必填                      |
| `ACCOUNT__BYOK_VAULT_TOKEN`           | 空                          | Vault token；`vault` 模式必填                     |
| `ACCOUNT__BYOK_VAULT_MOUNT`           | `transit`                   | Transit 挂载点                                    |
| `ACCOUNT__BYOK_VAULT_KEY`             | `byok`                      | Transit key 名                                    |
| `ACCOUNT__BYOK_VAULT_TIMEOUT_SECONDS` | `5`                         | Vault HTTP 超时                                   |
| `ACCOUNT__INTERNAL_SECRET`            | 空                          | 内部端点鉴权；**留空 = BYOK 解析端点关闭**（401） |

### 8.2 llm-gateway（`LLM_GATEWAY__` 前缀）

| 变量                                           | 默认                    | 说明                                                                              |
| ---------------------------------------------- | ----------------------- | --------------------------------------------------------------------------------- |
| `LLM_GATEWAY__INTERNAL_SECRET`                 | 空                      | **同时是内部鉴权与 BYOK 总开关**：留空 → 模型端点不鉴权且 BYOK 停用（仅本地 dev） |
| `LLM_GATEWAY__BYOK_ENABLED`                    | `true`                  | 显式关掉租户 key 解析（配合内部密钥形成双层开关）                                 |
| `LLM_GATEWAY__ACCOUNT_BASE_URL`                | `http://127.0.0.1:8001` | 解析地址；容器内必须覆盖                                                          |
| `LLM_GATEWAY__ACCOUNT_TIMEOUT_SECONDS`         | `5`                     | 解析超时（超时即回退平台 key）                                                    |
| `LLM_GATEWAY__BYOK_CACHE_TTL_SECONDS`          | `300`                   | 正缓存 TTL = 轮换生效延迟上限                                                     |
| `LLM_GATEWAY__BYOK_NEGATIVE_CACHE_TTL_SECONDS` | `30`                    | 404 负缓存                                                                        |
| `LLM_GATEWAY__BYOK_CACHE_MAX_ENTRIES`          | `1024`                  | LRU 上限                                                                          |

### 8.3 调用方与编排

| 位置                      | 变量                                       | 说明                                                                       |
| ------------------------- | ------------------------------------------ | -------------------------------------------------------------------------- |
| agent-orchestrator        | `AGENT_ORCHESTRATOR__INTERNAL_AUTH_SECRET` | 由 `_identity_headers_hook` 注入 `X-Internal-Secret`；租户头取自请求上下文 |
| knowledge-service         | `KNOWLEDGE__INTERNAL_SECRET`               | 嵌入走网关时的内部密钥；留空则网关拒绝解析（回退平台 key）                 |
| deploy/docker-compose.yml | `INTERNAL_SECRET`                          | account / AO / gateway / knowledge / tool / bff 同源注入                   |

**一致性要求**：account、gateway、orchestrator、knowledge、tool-service、BFF 的 `internal_secret` 必须是同一个值。本地 dev 不配也能跑（account 端点关闭、gateway 不鉴权、BYOK 停用），但这意味着 BYOK 完全不生效——这是最容易踩的坑。

**编排要求**：`deploy/docker-compose.yml` 已给 llm-gateway 注入 `LLM_GATEWAY__INTERNAL_SECRET` 与 `LLM_GATEWAY__ACCOUNT_BASE_URL=http://account-service:8001`（2026-10-26 补齐）。自建编排必须显式覆盖后者，否则容器内默认 `127.0.0.1:8001` 指向网关自身 → 解析一律 `error` → 静默回退平台 key（见 [deploy/web/README.md](../../deploy/web/README.md) §4.1）。

## 9. 威胁模型与限制

| 威胁                              | 缓解                                                                                          | 残余风险                                                             |
| --------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| 数据库备份/误导出泄漏租户 key     | 信封加密；KEK 不在库里（vault）或至少是独立配置（local）                                      | local 模式下整机失守 = KEK 与密文同时泄漏                            |
| 内网任意进程调用 `/secret` 拿明文 | 内部密钥 + 租户头；密钥留空时端点关闭                                                         | 内部密钥是全局共享单值，泄漏面是整个内网；无 per-caller 身份         |
| 跨租户越权读 key                  | 租户来自 JWT claim / 上游上下文；内部端点强制 `X-Tenant-Id`；仓储按租户过滤（跨租户一律 404） | 网关信任上游头的租户值——上游若被攻破，可指定任意租户                 |
| 日志/事件泄漏                     | 只落 `key_hint`、`source`、`kid`；禁落清单见 observability                                    | 调试时手工打开 `LOG_CONTENT` 不会带 key（key 不在请求体里）          |
| 明文在内存/内网被抓取             | 内部 HTTP + `no-store`；不做磁盘缓存                                                          | 内网明文 HTTP（compose 默认），生产应叠加 mTLS/服务网格              |
| 恶意的 `api_base`                 | 不支持（D8）                                                                                  | —                                                                    |
| 缓存串租户                        | 缓存键含 `tenant_id`                                                                          | —                                                                    |
| BYOK 故障放大成整体不可用         | 全部失败路径回退平台 key                                                                      | 用户以为在用自己 key、实际烧平台额度——只能靠指标/日志发现            |
| 无限流                            | 无 per-tenant 速率隔离                                                                        | 一个租户的 key 故障不会影响他人，但 BYOK 不提供配额保护（属其他 P0） |
| 批量轮换被滥用                    | 内部端点鉴权（与解析共用同一把内部密钥）                                                      | 无分页、无管理员级独立鉴权：持密钥者可一次 rewrap 全部租户条目       |

**明确不承诺**：不防平台管理员（能读 KMS 配置即可解密）；不做 per-tenant 限流；不做密钥有效性校验（保存时不会去试打，配错 key 只在真正调用时以 provider 错误体现）。

## 10. 回滚与降级

| 诉求                       | 操作                                          | 影响                                                                                                     |
| -------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 立刻关掉 BYOK              | `LLM_GATEWAY__BYOK_ENABLED=false` 重启网关    | 所有租户回平台 key；account 数据不动，可随时开回                                                         |
| 网关回滚到旧版本           | 回滚镜像                                      | 旧版本忽略租户头/不读 `/secret`，静默回退平台 key——可用但账单口径变化，需同步告知                        |
| 撤销全部租户 key           | 清空 account `INTERNAL_SECRET` 重启           | 解析端点关闭（401）→ 回退平台 key；该值若与网关共用，清空会连带取消模型端点鉴权，生产不建议这样止血      |
| account 回滚到信封 v2 之前 | —                                             | **会坏**：旧版本把信封当裸 token 解，得到 `500 byok.unreadable`。回滚前需先重录/导出 key，或保留 v2 版本 |
| vault → local              | —                                             | **不可逆**：local 解不开 vault 密文；需先用 vault 解密再以 local 重加密（或让租户重录）                  |
| KMS 不可用                 | `502 byok.kms_unavailable` → 网关回退平台 key | 调用面不 5xx；但轮换与 secret 端点不可用                                                                 |

原则：**BYOK 的每个开关都只影响「用谁的 key」，不影响「能不能出模型」**；唯一例外是回滚 account 版本（会破坏密文可读性），因此升级 account 时必须确认回滚路径。

## 11. 验证

### 11.1 自动化测试

| 范围         | 测试文件                                           | 覆盖点                                                                                |
| ------------ | -------------------------------------------------- | ------------------------------------------------------------------------------------- |
| account KMS  | `services/account-service/tests/test_byok_kms.py`  | 信封往返、历史 kid、裸 token、未知 kid、needs_rotation/rotate、vault（MockTransport） |
| account 端点 | `services/account-service/tests/test_insights.py`  | 掩码、provider 归一、secret 端点鉴权与 no-store、rotate 逐条容错                      |
| 网关解析     | `services/llm-gateway/tests/test_byok.py`          | disabled/negative/error/byok/cache_hit 五态、缓存与 LRU、单飞、回退                   |
| 内部鉴权     | `services/llm-gateway/tests/test_internal_auth.py` | `X-Internal-Secret` 比较、缺失/错误密钥                                               |

门禁结果（2026-10-26，本轮改动后）：account-service 62 passed / 78.91%（阈值 77）；llm-gateway 70 passed / 97.58%（阈值 95）；knowledge-service 26 passed；agent-orchestrator 663 passed（阈值 82）。

### 11.2 手工冒烟（详见 [local-dev.md](../local-dev.md) §4.1）

1. 三个服务配同一个 `INTERNAL_SECRET`（account / gateway / knowledge），重启；
2. `PUT /v1/byok/deepseek` 存一把 key，确认列表只回 `••••` + 末 4 位；
3. 发一条对话，看网关日志 `byok_resolved` 的 `key_hint` 是否与录入一致；
4. 删掉 key（或清空网关缓存），确认回退平台 key、指标 `negative` 抬头；
5. `POST /v1/byok/rotate` 返回 `failed=0`，再读一次 secret 确认可解。

### 11.3 已知遗留（本轮未处理，见 §12 / 06 册）

| 位置                                    | 现象                                                                 |
| --------------------------------------- | -------------------------------------------------------------------- |
| `docs/gap-2026-10/01-repo-inventory.md` | 审计快照仍称「有壳无实」；按旧册约定只留痕，不回改                   |
| 用量记账                                | `usage_events` 不记来源，无法区分 byok/platform 成本（06 P0-1 范围） |

## 12. 后续工作

1. **审计表**：网关落 `byok_audit(tenant, provider, key_hint, key_id, used_at)`（采样或按请求），支撑「谁在用 BYOK」与合规查询；
2. **成本归因**：`usage_events` 加 `key_source`（byok/platform），让「BYOK 不扣 credits」可被账单验证（06 P0-1）；
3. **租户级限流**：BYOK 不提供速率保护，per-tenant 并发/速率闸仍在 06 清单里；
4. **密钥有效性校验**：保存时可选做一次轻量探活（如 `GET /models`），把「配错 key」从调用期提前到保存期；
5. **前端状态**：面板显示「已生效 / 上次使用时间 / 最近一次解析失败」；
6. **Vault 生产化**：HA、令牌轮转、Transit key 轮换与 per-tenant key 隔离；
7. **内网加密**：mTLS / 服务网格，消除内部明文 HTTP 的残余风险；
8. **rotate 运维面**：分页 / 按 provider 过滤与管理员级独立鉴权，避免单次全租户扫描。
