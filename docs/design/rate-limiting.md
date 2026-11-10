# 限流后端:进程内与可选 Redis(P0-5)

> 状态:已落地(2026-11-10)。范围:`packages/common/common/security/rate_limit.py` 的限流抽象,
> api-gateway 的 `/api` 中间件与 account-service 的 OAuth 动态注册限流。相关:M0-03
> (云 PG/云 Redis 接入,见 [roadmap.md](../roadmap.md))、M0-05/06 网关鉴权链路。

## 1. 现状

限流器在 `common.security` 里,接口是 `async def allow(key: str) -> bool`。历史实现只有
`SlidingWindowLimiter`(进程内)每次调用持 `asyncio.Lock`,清理 `window_seconds` 之前的时间戳,
再按 `len(deque) < limit` 决定放行。

进程内实现在多副本下有两个问题:

- 每个副本各记一份,总限额变成 `副本数 × limit`(`docs/roadmap.md` 的 R-P1-8 缺口);
- 进程重启计数清零,窗口被"刷新"。

但进程内实现零依赖、单测零网络,保留为默认与回退。本次新增可选 Redis 后端,由 `REDIS_URL` 配置切换。

## 2. 抽象与两种实现

```python
class RateLimitStore(Protocol):
    async def increment(self, key: str, ttl_seconds: int) -> int: ...
```

- **`SlidingWindowLimiter(limit, window_seconds=60.0)`**:进程内滑动窗口(默认)。`aclose()` 为空实现,便于统一回收。
- **`RedisFixedWindowLimiter(limit, window_seconds=60.0, *, store, namespace="rl", clock=time.time)`**:
  Redis 固定窗口,计数经 `store.increment()` 落库。
- **`RedisRateLimitStore(client, *, namespace)`**:注入 `redis.asyncio.Redis`(要求 `decode_responses=True`),
  `increment` 在单个 pipeline 里执行 `INCR` + `EXPIRE ttl`(原子,避免 INCR 成功而 EXPIRE 丢失导致键永不过期),
  键为 `f"{namespace}:{key}"`;`aclose()` 关闭连接池。
- **`build_rate_limiter(*, limit, window_seconds=60.0, redis_url, namespace)`**:工厂。`redis_url` 为空 →
  `SlidingWindowLimiter`;有值 → `redis.asyncio.from_url(redis_url, decode_responses=True)` +
  `RedisRateLimitStore` + `RedisFixedWindowLimiter`。连接是惰性的,模块导入不发网络,Redis 不可用时首个请求才失败
  (fail-open,见 §4)。

Redis 后端不用滑动窗口,而用**固定窗口**,理由:滑动窗口要么把每条时间戳放进 ZSET(按请求数增长的内存与带宽),
要么用多键近似;固定窗口只需一个整数键 + 一次 pipeline,代码与运维成本最低,代价是窗口交界处允许最多 2 倍瞬时通过。
限流目的是"挡住刷接口/暴力注册",2 倍尖峰可接受,不引入令牌桶的复杂度。

## 3. 键设计

| 使用点                    | 传入 key                                               | 最终 Redis 键                                 | 窗口  |
| ------------------------- | ------------------------------------------------------ | --------------------------------------------- | ----- |
| api-gateway `/api` 中间件 | `{account 或 client_host}:{group}`,group 取路径前 4 段 | `api-gateway:{bucket}:{account}:{group}`      | 60s   |
| account OAuth 动态注册    | `oauth-register:{client_ip}`                           | `account:oauth-register:{bucket}:{client_ip}` | 3600s |

`bucket = int(clock() // window_seconds)`,所以键随窗口翻转;TTL 取 `int(window_seconds) * 2`(最少 1 秒),
旧桶最多留两个窗口后自然过期,无需清理任务,避免"键无 TTL 永久封禁"。
namespace 隔离不同服务/用途的计数;未登录请求用 `client_host`,已登录用 account id。
api-gateway 的 `rate_limit_per_minute`(默认 120)与 account 的 `oauth21_register_limit_per_hour`(默认 10)是各自配额,
Redis 只改变计数位置,不改变额度。

## 4. fail-open 取舍

后端(Redis)异常时 `RedisFixedWindowLimiter.allow()` 记一条结构化 warning(`rate_limit.backend_error`,
含 namespace/key)后**放行**:

- 限流是**可用性保护**,不是鉴权边界。Redis 抖动时把全部流量 429,等于用限流器自己制造故障;
- 鉴权/配额/审计仍在各自服务内做,放行的请求不会绕过它们;
- 单条注释与 warning 保证降级可见,而不是静默失效。

代价:Redis 故障期间限流形同虚设(等于回退到无限制)。若某天限流被用于计费或硬性风控,应重新评估该默认
(可加 fail-closed 配置项),当前不属于此类。

## 5. 配置与部署

两个服务各自读 `<PREFIX>__REDIS_URL`(pydantic-settings,默认 `None`):

```sh
# api-gateway
API_GATEWAY__REDIS_URL=redis://redis:6379/0
# account-service
ACCOUNT__REDIS_URL=redis://redis:6379/0
```

`deploy/docker-compose.yml` 已给出这两个变量(指向 compose 内的 `redis:7-alpine`),并让两服务
`depends_on` redis `service_healthy`;注释掉变量即回退进程内。本地开发不配时行为与旧版完全一致。
生产多副本时所有副本必须配同一个 Redis(同一 DB/namespace),否则各副本仍各记一份。

进程退出时 `create_app` 注册的 lifespan 会 best-effort `await limiter.aclose()`(关闭 Redis 连接池);
回收失败只记 `rate_limit.close_failed` warning,不阻止退出。

## 6. 与 M0-03 的关系

`docs/roadmap.md` 的 M0-03 是"云 PG/云 Redis 接入",其中包含把限流从进程内切到 Redis。本次(P0-5)先把
**代码侧**的可选 Redis 后端落地:注入云 Redis URL 即生效,不需要再改代码。M0-03 剩余的是环境与运维面:
生产 URL/凭证注入、云 Redis 规格与网络、监控告警与 CI 覆盖;这些不在本任务范围。

## 7. 残余风险

- **无路由级配额**:只有 api-gateway 的路径前缀分组与注册接口两个固定配额,没有按账号/套餐的差异化配额;
- **无 Redis 集群/Sentinel**:`from_url` 单连接串,未做故障转移与读副本;Redis 单点故障时按 §4 降级为不限流;
- **无指标**:没有暴露限流命中/拒绝计数(只有 warning 日志),无法直接做告警面板;
- **固定窗口边界**:窗口交界最多 2 倍瞬时通过(见 §2);
- **进程内后端仍非共享**:未配 `REDIS_URL` 的多副本部署仍是每副本各自限额,这是默认行为而不是 bug。
