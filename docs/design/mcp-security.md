# MCP 出站安全（SSRF 校验与降级可见）

> 状态：P0-6 已落地（2026-11-09）。本文是 MCP 出站地址校验与工具面降级可见的唯一契约；协议版本对齐（P1-H）见 [mcp-dual-stack.md](./mcp-dual-stack.md)。

## 1. 目标与范围

MCP server 的 URL 由用户填写、由服务端直连，因此和 `fetch`/`file_capture` 一样属于**出站请求**：不校验就等于把内网探测与云元数据读取能力交给任何能建 MCP 预设的账号。本文约束三件事：

1. **注册期**：明显非法的地址（字面 IP、localhost 名）在写库前就拒绝；
2. **连接期**：真正发请求前解析域名并拒绝内网/保留地址，覆盖 `initialize`、`tools/list`、`tools/call`、手动 `/test`；
3. **降级可见**：自动加载工具失败不再静默，落日志（手动 `/test` 另落 `last_status`/`last_error`）。

范围：HTTP 型 MCP server（含 OAuth token 路径）。stdio 型不走 URL，由 `_require_command_allowed` 管可执行命令白名单，不在本文范围。

## 2. 威胁模型

| 攻击路径       | 例子                                              | 后果                          |
| -------------- | ------------------------------------------------- | ----------------------------- |
| 云元数据       | `http://169.254.169.254/latest/meta-data/`        | 读取实例凭证                  |
| 内网服务       | `http://10.0.0.5:8080/admin`、`http://redis:6379` | 内网横向探测/读写             |
| 本机回环       | `http://127.0.0.1:8008`、`http://localhost`       | 绕过鉴权打内部 API            |
| IPv6 私网/映射 | `http://[::1]`、`http://[::ffff:127.0.0.1]`       | 同上（IPv4 映射需归一后判）   |
| CGNAT/保留段   | `100.64.0.0/10`、`0.0.0.0`、组播                  | 特殊网络可达性                |
| DNS rebinding  | 首次解析公网、连接时解析私网                      | 绕过一次性校验（残余，见 §7） |

## 3. 防线一：注册期快速拒绝

`services/agent-orchestrator/agent_orchestrator/schemas/mcp.py` 的 `MCPServerCreate._check_http()` 在 `http(s)://` 前缀校验之外，对**字面量**地址直接 ValueError：

- 字面 IP（v4/v6）落在私网/回环/链路本地/保留/组播/未指定/CGNAT → 拒绝；
- `localhost` / `*.localhost` 名 → 拒绝。

这一层零网络、纯语法判断，目的是让明显错误的配置在填表时就失败，而不是等到执行工具才报错。域名类地址（可能解析到公网）不在这里预解析——注册期做 DNS 会让保存动作依赖外部网络，见 §7。

## 4. 防线二：连接前解析校验

单一真源：`services/agent-orchestrator/agent_orchestrator/services/net_guard.py`。

### 4.1 判定集合

`is_blocked_address(address)` 用 `ipaddress` 判定，IPv6 先做 `ipv4_mapped` 归一，命中任一条即拒绝：

- `is_private`、`is_loopback`、`is_link_local`、`is_reserved`、`is_multicast`、`is_unspecified`；
- 显式加入的 `CGNAT_NETWORK = 100.64.0.0/10`（`is_private` 不覆盖 CGNAT）；
- 解析不出 IP 视为不可信（`is_blocked_address` 返回 True）。

`resolve_host_sync(host)` 走 `socket.getaddrinfo`，调用方用 `asyncio.to_thread` 包装，避免阻塞事件循环。

### 4.2 校验入口与时机

`MCPClient._post()` 在**第一次发包前**调用 `net_guard.assert_url_allowed(self._url, ...)` 并把结果 memo（`self._url_checked`）。因为 MCP 握手必先 `initialize`，这一次校验覆盖后续 `tools/list`、`tools/call`；`/api/v1/mcp/servers/{id}/test` 复用同一 client，因此也被覆盖。

`assert_url_allowed` 的顺序：

1. 取不到 host（如 `file:///etc/passwd`）→ 拒绝；
2. host 精确命中 `MCP_ALLOWED_HOSTS`（小写比较）→ 放行（不解析 DNS）；
3. host 是字面 IP → 直接判定，不解析 DNS；
4. host 是 localhost 名 → 拒绝；
5. 解析域名：任一地址被 blocked → 拒绝；全部公网 → 放行。

命中时抛 `BlockedTargetError(host, addresses)`，消息形如「目标地址指向内网,已拒绝连接:{host}(命中 {address})。如确需访问内网 MCP server,请把它加入 MCP_ALLOWED_HOSTS 白名单。」

### 4.3 白名单

自建内网 MCP server 是合法需求，用 `MCP_ALLOWED_HOSTS`（CSV/列表，按 host 精确匹配、大小写不敏感）开口子。白名单命中即跳过解析，意味着**白名单里的 host 不再受内网判定约束**——这是显式的运维决策，不要放通用域名。

### 4.4 失败语义（与下载路径的差异）

- **MCP 连接**：DNS 解析失败（`OSError`，含测试环境下 `pytest-socket` 拦截）→ 记 `logger.debug` 后**放行**，让真实连接自己报错。理由：解析不到不等于可达，把 DNS 抖动变成「配置非法」会制造噪声故障。
- **交付物下载**（`file_capture.HttpFileDownloader`）：解析失败 → `artifact.download_failed`，**拒绝**。下载会落盘，收益低风险高，口径更严。

两者共用 `is_blocked_address`/`resolve_host_sync`，但失败策略各自决定，不要合并成一个「统一策略」。

### 4.5 错误如何到用户

- 手动 `/test`：`BlockedTargetError` 被 `test_server` 捕获，`last_status="error"`、`last_error="BlockedTargetError: ..."`，前端 MCP 面板可见；
- 工具调用：`_make_http_handler` 的异常兜底把它回喂成 JSON 错误文本给模型，不中断 run。

## 5. 降级可见

`graph/nodes/toolkit.py` 的 `_load_toolkit` 对「外部工具（MCP/知识/记忆/技能）加载失败」保持降级（`extras = []`，仍能用基础工具），但不再静默：`except Exception as exc` 记 `logger.warning("tool extras load failed; falling back to base tools", account_id=..., error=...)`。graph 层不 import services，符合既有分层。

## 6. 配置与装配

| 项                  | 位置                                                    | 说明                                                                   |
| ------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------- |
| `MCP_ALLOWED_HOSTS` | `core/config.py` 的 `mcp_allowed_hosts: list[str] = []` | 出站白名单（host 精确匹配）                                            |
| 装配                | `core/container_tools.py` 的 `_assemble_tools`          | 启动时 `configure_url_guard(allowed_hosts=settings.mcp_allowed_hosts)` |
| 复位                | `core/container.py` 的 `stop()`                         | `reset_url_guard()`，避免同进程下一个容器继承上一个白名单              |
| 测试注入            | `mcp_client.configure_url_guard(resolver=...)`          | 单测用假 resolver，零 DNS                                              |

## 7. 残余

- **DNS rebinding TOCTOU**：校验与真正连接是两次解析，绑定型攻击仍有窗口。彻底解法是 connect 到已校验 IP（改 transport/自定义 resolver），成本高，未做。
- **注册期不做 DNS 预校验**：域名类地址保存时只过语法；是否可达留给 `/test`。
- **stdio 型未覆盖**：本机命令由命令白名单约束，不涉及 URL。
- **无指标/事件**：只落日志与 `last_error`，没有「出站被拦」计数；多副本各自读同一份配置，无集中下发。
- 协议版本对齐（`2025-03-26` → `2026-07-28` 双栈）属 P1-H，与本文正交。

## 8. 验证

```sh
cd services/agent-orchestrator
uv run pytest tests/test_net_guard.py tests/test_mcp.py -q --no-cov
```

覆盖点：地址判定矩阵（v4/v6/映射/CGNAT/元数据）、白名单绕过解析、解析失败放行、字面 IP 不触发 DNS、localhost 名直接拒、client 在发包前被拦、`/test` 落 `error`、toolkit 降级日志。
