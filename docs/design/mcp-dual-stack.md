# MCP 双栈（P1-H）契约：2025-03-26 与 2026-07-28

> 状态：已落地（2026-11-10，落地位置见 §9）。本文是 P1-H 的唯一实现契约；出站 SSRF/地址校验见
> [mcp-security.md](./mcp-security.md)，两者正交。

## 1. 目标与范围

同一部署同时服务两代 MCP 客户端：

- **服务端**：`POST /mcp` 按 `MCP-Protocol-Version` 请求头分流——`2026-07-28` 走无状态自包含语义，
  缺失或 `2025-03-26` 走现有有状态会话语义；新增 `server/discover`。
- **客户端**（连接器 → 外部 MCP server）：默认自动协商（先探 `2026-07-28`，失败回落旧版
  `initialize`），也可用配置强制某一版本。
- **不做**：`elicitation`、`tasks` 扩展、`subscriptions/listen`（本期直接 `-32601`）、
  `sampling`/`roots`/`logging`（2026-07-28 已弃用，永不补）。

验收口径：2026-07-28 客户端可直连且无任何会话状态依赖；2025-03-26 客户端零回归。

## 2. 版本常量与分流

| 常量                          | 值                    | 用途                              |
| ----------------------------- | --------------------- | --------------------------------- |
| `PROTOCOL_VERSION_LEGACY`     | `"2025-03-26"`        | 有状态会话栈                      |
| `PROTOCOL_VERSION_STATELESS`  | `"2026-07-28"`        | 无状态自包含栈                    |
| `SUPPORTED_PROTOCOL_VERSIONS` | `(STATELESS, LEGACY)` | 错误响应与 `server/discover` 公告 |

- 服务端判定顺序：头缺失 → legacy；头 ∈ 支持列表 → 对应栈；头为其他值 → HTTP 400 +
  JSON-RPC `-32600`，`data.supported_versions` 列出支持版本。
- 两个栈共用同一鉴权（API Key / OAuth introspect）、scope 校验、工具/资源目录与 SSRF 出口约束；
  分流只决定「握手与会话语义」，不改变授权面。

## 3. 服务端（`api/mcp_server.py` + `services/mcp_server.py`）

### 3.1 无状态栈（2026-07-28）

- 不签发 `Mcp-Session-Id`，不要求也不依赖 `initialize`；每个请求自包含、可独立执行
  （鉴权已按请求做）。
- 响应附加 `MCP-Protocol-Version: 2026-07-28`；`GET /mcp` 返回 405，`DELETE /mcp` 返回 204
  （无会话可删，保留幂等语义）。
- `initialize` 仍**宽容接受**（部分新客户端仍先握手）：返回
  `{protocolVersion:"2026-07-28", capabilities, serverInfo}`，但不建会话、不回 session 头。
- `server/discover`（无 scope 要求，仅需已认证）：

  ```json
  {
    "protocolVersions": ["2026-07-28", "2025-03-26"],
    "protocolVersion": "2026-07-28",
    "serverInfo": { "name": "beauty-moon", "version": "0.1.0" },
    "capabilities": { "tools": {}, "resources": {} },
    "instructions": "公开 MCP 只暴露只读工具与交付物资源"
  }
  ```

- 方法表：`server/discover`、`ping`、`tools/list`、`tools/call`、`resources/list`、
  `resources/read`；`subscriptions/listen` 与其余未知方法 → JSON-RPC `-32601`
  （message 注明本部署不支持）。
- 每请求能力协商：`params._meta` 里的 `clientInfo`/`capabilities` 允许出现，服务端忽略未知字段，
  绝不因未知能力拒绝请求（向前兼容）。

### 3.2 有状态栈（2025-03-26，现状不变）

- `initialize` 返回 `protocolVersion:"2025-03-26"` + 现有 `capabilities`；会话、SSE、scope 行为全部保持。
- 该栈不新增 `server/discover`（旧客户端不会调；若调用回 `-32601`）。

## 4. 客户端（`services/mcp_client.py`）

### 4.1 构造与配置

- `MCPClient(..., protocol_version: str | None = None)`：`None` = 自动协商；`"2025-03-26"`、
  `"2026-07-28"` = 强制。
- `mcp_runtime.build_client` 透传 `settings.mcp_protocol_version`；stdio 路径不受影响
  （stdio 只用旧版语义，见 `mcp_stdio.py`）。

### 4.2 自动协商（`protocol_version is None`）

首个请求前（SSRF 校验之后）用 `POST server/discover`、头
`MCP-Protocol-Version: 2026-07-28` 探测：

- 2xx 且 `result.protocolVersions` 或 `result.protocolVersion` 含 `2026-07-28`
  → 锁定无状态栈，`logger.info("mcp.protocol.negotiated", protocol_version=...) ` 一次。
- HTTP 400/404/405、JSON-RPC `error`、空 `result`、连接失败或超时 → 回落 legacy：
  `logger.warning("mcp.protocol.discover_failed", ...)` 后走原有 `initialize`。
- HTTP 401 → 仍抛 `MCPAuthRequired`（授权问题不当作版本不兼容）。
- 协商结果在 client 生命周期内 memo；不重试 discover（下次新建 client 再探）。

### 4.3 无状态请求

- 不发 `initialize` / `notifications/initialized` / `Mcp-Session-Id`；`aclose` 不发 `DELETE`。
- 每个请求都带 `MCP-Protocol-Version: 2026-07-28`；`Accept: application/json, text/event-stream`
  与单帧 SSE 解析保持现状（宽容兼容）。
- 不做「会话失效 → 重连」分支（无会话）。

### 4.4 强制版本

- `"2025-03-26"`：跳过 discover，直接 legacy `initialize`。
- `"2026-07-28"`：不回落。discover 失败即抛 `MCPError`（消息含 server 返回详情），
  避免把配置错误静默掩盖。

## 5. 配置

| 位置               | 键                     | 默认     | 说明                                                                                 |
| ------------------ | ---------------------- | -------- | ------------------------------------------------------------------------------------ |
| agent-orchestrator | `mcp_protocol_version` | `"auto"` | `auto` / `2025-03-26` / `2026-07-28`；env `AGENT_ORCHESTRATOR__MCP_PROTOCOL_VERSION` |

服务端无开关：按请求头分流；旧客户端不带头，天然落 legacy。

## 6. 测试（零网络，`httpx.MockTransport`）

客户端（`tests/test_mcp.py` 或新 `test_mcp_dual_stack.py`）：

1. auto + discover 成功 → 只发 discover，不发 initialize/session/DELETE，每请求带新版本头。
2. auto + discover 404/400/JSON-RPC error → 回落 legacy initialize，后续行为与现状一致。
3. auto + discover 401 → `MCPAuthRequired`，不回落。
4. 强制 legacy → 不发 discover。
5. 强制 stateless + discover 失败 → 抛 `MCPError`，不回落。
6. legacy 会话失效（404）→ 重连行为不回归。

服务端（`tests/test_mcp_server.py`）：

7. 头 `2026-07-28`：`server/discover` 返回两版本、响应无 `Mcp-Session-Id`、
   `tools/list` 免 initialize 即可用（scope 仍校验）。
8. 无头 / 头 `2025-03-26`：`initialize` 回 `2025-03-26`（现有用例不动）。
9. 未知版本头 → HTTP 400 + `-32600` + `supported_versions`。
10. 无状态栈 `subscriptions/listen` → `-32601`；`GET /mcp` 405、`DELETE /mcp` 204。

## 7. 兼容矩阵（灰度口径）

| 客户端               | 服务端    | 结果                   |
| -------------------- | --------- | ---------------------- |
| 无版本头（旧 SDK）   | 本部署    | legacy 会话，零回归    |
| 头 `2025-03-26`      | 本部署    | legacy                 |
| 头 `2026-07-28`      | 本部署    | 无状态                 |
| 本仓库 client `auto` | 新 server | discover 成功 → 无状态 |
| 本仓库 client `auto` | 老 server | discover 失败 → legacy |

默认 `auto` 即灰度：新连接器走新栈，老 server 自动回落；需要复现问题时用配置强制版本。

## 8. 残余与后续

- `subscriptions/listen` 未做：需要长连接与逐账号事件源，本期公开 server 无此馈送。
- `elicitation`、`tasks` 扩展未做（本期不涉及交互式补参/长任务协议）。
- auto 把 5xx 也当「版本不兼容」回落，可能掩盖上游真实故障——靠 `discover_failed` warning 留痕；
  强制版本可绕开。
- 服务端对无状态请求不做「能力必需性」校验，只忽略未知 `_meta` 字段。

## 9. 落地位置（2026-11-10）

| 层               | 位置                                                                                     | 说明                                                                                                                                                                  |
| ---------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 服务端方法分派   | `agent_orchestrator/services/mcp_server.py`                                              | `PROTOCOL_VERSION_LEGACY/STATELESS`、`SUPPORTED_PROTOCOL_VERSIONS`、`MCPMethodNotFoundError`（-32601）、`discover_result()`、`handle_jsonrpc(..., protocol_version=)` |
| 服务端 HTTP 分流 | `agent_orchestrator/api/mcp_server.py`                                                   | `_resolve_version`（缺失/旧版 → legacy，未知 → 400 + `-32600`）、`_version_headers`（仅无状态回带）、`DELETE /mcp`（无状态 204 / 旧栈 405，两者均不进 OpenAPI）       |
| 客户端协商       | `agent_orchestrator/services/mcp_client.py`                                              | `normalize_protocol_version`、`_ensure_protocol` / `_discover` / `_handshake`；无状态路径不发 initialize / notifications / session 头 / DELETE                        |
| 装配             | `agent_orchestrator/services/mcp_runtime.py` + `core/container_tools.py`                 | `configure_protocol_version` → `build_client`                                                                                                                         |
| 配置             | `agent_orchestrator/core/config.py`                                                      | `mcp_protocol_version="auto"`                                                                                                                                         |
| 测试             | `tests/test_mcp_dual_stack.py`（客户端 8 例）、`tests/test_mcp_server.py`（服务端 4 例） | 零网络 `httpx.MockTransport`；既有 client / net_guard 测试显式锁 legacy                                                                                               |

偏离：无（`auto` 回落复用 `_handshake` 避免重复 initialize；无状态 `DELETE` 204 也回带版本头）。
验证：`npx nx lint agent-orchestrator` 通过；`npx nx test agent-orchestrator` 1139 passed（覆盖率 87.82%，阈值 82）；OpenAPI 快照未变。
