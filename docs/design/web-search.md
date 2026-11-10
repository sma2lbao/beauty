# web_search 工具契约（P1-L / M3）

状态：**契约定稿（2026-11-10）**，实现待落地（task-15）。上游背景见
[../gap-2026-11/00-refresh-2026-11-09.md](../gap-2026-11/00-refresh-2026-11-09.md) §5.2
与 [tool-execution.md](./tool-execution.md) §9（M3）。

## 1. 目标与非目标

**目标**：给模型一个"查公网"的只读工具，返回结构化搜索结果（标题 / URL / 摘要），
模型据此在回答里用 markdown 链接引用来源。**只读、无账号凭证、无写副作用。**

**非目标**：不做网页正文抓取（那是 `fetch`）、不做搜索缓存、不做结果排序重排、
不做多轮追问、不做垂直搜索（图片/新闻/学术）。

## 2. 现状与复用

- 目录真源在代码：`services/tool-service/tool_service/services/catalog.py`（不落库）。
- HTTP 执行器：`services/tool-service/tool_service/services/executors/http.py`
  （`HttpToolExecutor.execute` 按工具名分派；已有 `search_knowledge` / `fetch`）。
- 出口校验复用 `services/tool-service/tool_service/services/executors/fetch.py` 的
  `is_public_address()` / `parse_target()`：私网、回环、链路本地、CGNAT、保留地址一律拒绝。
- 能力开关口径与 `fetch` / `exec` 一致：**关闭的工具在目录、详情、执行三处都"不存在"**，
  由 `services/tool-service/tool_service/services/execution/arguments.py::is_available` 统一判定。

## 3. 工具契约（目录描述符）

```python
WEB_SEARCH = ToolDescriptor(
    name="web_search",
    description=(
        "在公网做网页搜索（只读），返回按相关度排序的标题、URL 与摘要。"
        "回答里引用来源时，必须使用结果里的 url 原文写成 markdown 链接，"
        "不要编造或改写 URL；摘要不足以回答时再对具体 url 调 fetch 抓正文。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 500},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ,
    kind=ToolKind.FUNCTION,
    executor=ExecutorKind.HTTP,
    timeout_seconds=20.0,
    enabled=True,          # 目录启用；真正可见由 search_enabled + 配置完整度决定
)
```

- `risk=READ`：进对话只读工具集（`_chat_toolkit`），无需确认；不进 plan 危险闸。
- 结果必须落在 `MAX_TOOL_CHARS=25000` 以内（见 §5 截断），否则按失败返回
  `tool.too_large`（理论上不可达，保险）。

## 4. 结果信封

成功（`ok=true`）：

```json
{
  "query": "用户查询原文",
  "provider": "tavily",
  "results": [
    {
      "rank": 1,
      "title": "标题",
      "url": "https://example.com/a",
      "snippet": "摘要片段"
    }
  ],
  "notes": ["引用来源请使用 results[].url 原文"]
}
```

- `rank` 从 1 连续递增，按提供方返回顺序（不重排）。
- `title` ≤ 300 字符；`snippet` ≤ 400 字符（超出截断并补 `…`）；`url` 最多 2048 字符。
- `notes` 仅在有截断时出现（"摘要已截断"），无截断则省略。
- 空结果不是错误：`results=[]` + `notes=["未找到结果，可换个更短的关键词"]`，`ok=true`。

## 5. 提供方适配（两种，均为服务端出站）

| provider         | 请求                                                                                                                                                        | 解析                                                                    |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `tavily`（默认） | `POST {base}/search`，JSON `{"api_key": key, "query": q, "max_results": n, "search_depth": "basic", "include_answer": false, "include_raw_content": false}` | `results[].{title,url,content,score}` → title/url/snippet=content       |
| `brave`          | `GET {base}/res/v1/web/search?q=<q>&count=<n>`，头 `X-Subscription-Token: key`、`Accept: application/json`                                                  | `web.results[].{title,url,description}` → title/url/snippet=description |

- 默认 base：tavily `https://api.tavily.com`、brave `https://api.search.brave.com`；
  `search_base_url` 非空时覆盖（自建网关场景）。
- 提供方 URL 由**部署方配置**，模型无法传入 URL；因此不做模型级 SSRF 面，但仍要做
  出口地址校验（§6）。
- `max_results` 夹到 `[1, settings.search_max_results]`（默认 10，提供方更小时以提供方为准）；
  请求数小于 `search_default_results`（5）时按请求值。

## 6. 安全与出口

- 只有 `https` 允许（`search_base_url` 也必须 https；`http` 直接判配置错误启动即报错）。
- 请求前解析 base host 并过 `is_public_address()`：解析结果为私网/回环/链路本地/CGNAT/
  保留地址 → 拒绝（`tool.search_blocked`，不重试）。解析失败 → 拒绝（配置指向不可解析主机
  属于部署错误，不能 fail-open）。
- 不跟随重定向（`follow_redirects=False`），3xx 视为失败。
- 响应体上限 1 MiB（`search_max_bytes`），超限截断读取后放弃解析 → `tool.upstream_bad_response`。
- 密钥只从配置读（`TOOL__SEARCH_API_KEY`），**不得**出现在结果、错误 hint、审计的 arguments 之外，
  也不得写日志；`log_content=false` 时不记 query 原文（沿用既有观测口径）。

## 7. 配置（`services/tool-service/tool_service/core/config.py`）

```python
search_enabled: bool = False            # 默认关；属于 NETWORK_SWITCHES，测试钉 False
search_provider: str = "tavily"         # tavily | brave
search_api_key: str = ""                # 空 = 未配置，工具不可见
search_base_url: str = ""               # 空 = 用提供方默认 https 地址
search_timeout_seconds: float = 10.0    # 单次搜索超时（descriptor 20s 内）
search_default_results: int = 5
search_max_results: int = 10
search_max_bytes: int = 1024 * 1024
```

- `is_available("web_search", settings)` 返回
  `settings.search_enabled and settings.search_provider in {"tavily","brave"} and bool(settings.search_api_key)`。
- `main.py` 加 `build_search_client(settings) -> SearchClient | None`（不可用返回 None），
  构造 `HttpToolExecutor(..., searcher=client)`；shutdown 时 `aclose`。
- 新文件 `services/tool-service/tool_service/services/executors/web_search.py`：
  `SearchClient`（`timeout_seconds` / `max_bytes` / `transport` / `resolver` 可注入）、
  `SearchError(code, message, hint, retryable)`、`SearchResult` 归一化函数。
  测试注入 `httpx.MockTransport`，**零网络**。

## 8. 错误码

| 场景                    | code                         | retryable | hint 口径                                               |
| ----------------------- | ---------------------------- | --------- | ------------------------------------------------------- |
| 参数非法（schema 层）   | `tool.invalid_args`          | false     | 指出字段                                                |
| 未配置（执行器为 None） | `tool.executor_unavailable`  | false     | "服务端未开启 TOOL\_\_SEARCH_ENABLED / 未配置 provider" |
| 出口地址被拒            | `tool.search_blocked`        | false     | 不暴露解析到的内网 IP                                   |
| 提供方 401/403          | `tool.search_auth_failed`    | false     | "搜索提供方鉴权失败，请检查 TOOL\_\_SEARCH_API_KEY"     |
| 提供方 429              | `tool.upstream_rate_limited` | true      | "搜索提供方限流，稍后重试"                              |
| 提供方 5xx / 连接失败   | `tool.upstream_unavailable`  | true      | 截断到 200 字符的响应体                                 |
| 超时                    | `tool.timeout`               | true      | "搜索超时（N 秒）"                                      |
| 非 JSON / 结构不符      | `tool.upstream_bad_response` | true      | 提供方名 + 状态码                                       |
| 结果超 25k              | `tool.too_large`             | true      | "降低 max_results"                                      |

## 9. 测试口径（零网络）

1. 目录：descriptor 存在、`risk=READ`、schema 校验（query 必填、max_results 1..10）。
2. 开关：`search_enabled=false` 时 `/v1/tools` 不出现、执行返回 404 `tool.not_found`；
   `search_enabled=true` 但 key/provider 缺失时同样不可见。
3. tavily 快乐路径：断言请求方法/URL/JSON body、解析 title/url/snippet、rank 连续。
4. brave 快乐路径：断言 GET query 参数、`X-Subscription-Token` 头、解析 `web.results`。
5. 截断：超长 title/snippet 截到上限并带 notes；`max_results` 超上限被夹住。
6. 错误映射：401/403、429、500、超时、非 JSON、空 body、重定向 302 各一条。
7. 出口：resolver 返回 `127.0.0.1` / `169.254.169.254` / 私网 → `tool.search_blocked`；
   解析抛异常 → 拒绝。
8. 对话可见性：`_chat_toolkit`（AO 侧既有过滤）里 web_search 属于 READ，
   由 AO 现有测试口径覆盖；tool-service 只保证 `risk=READ`。

## 10. 残余风险

- 结果质量与时效取决于提供方；无缓存会重复计费（成本口径：一次搜索 = 一次 provider 调用，
  不进 Langfuse generation，只在 tool span / 审计里可见）。
- 无提供方级并发限制/配额（依赖上游 429）。
- 摘要来自提供方，可能过时或含注入文本；模型侧靠既有"外部内容不可信"提示，
  本期不加额外清洗。
- `brave` 的 `count` 上限为 20、tavily 为 20，请求上限 10 已足够。
