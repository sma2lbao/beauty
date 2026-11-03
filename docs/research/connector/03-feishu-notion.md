# 飞书（Lark）与 Notion 接入调研 —— 连接器实现清单输入

> 调研时间 2026-11-03。方法：逐页读官方文档（飞书开放平台文档站是 SPA，但每页 URL 后加 `.md` 即返回纯 markdown；Notion 全站同样支持 `.md`，另有 <https://developers.notion.com/llms.txt>）。
> 可信度标记：**【事实】**＝官方文档/仓库代码原文可指认（带 URL 或路径）；**【推断】**＝基于事实的工程判断，原文未明写。
> 落地对象：`services/connector-service`（:8004），现状见 `services/connector-service/README.md` 与 `docs/architecture.md:79`、`:116`。

## 0. 结论先行

1. **【事实】飞书事件「长连接」只支持企业自建应用；商店应用（ISV）只能走 Webhook**（<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/long-connection-mode.md>）。且只有 ISV 资质用户才能创建商店应用（<https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview.md>）。**【推断】** 第一版接飞书应做**自建应用**，这意味着「每个客户企业各自建一个自建应用、把 App ID/Secret 交给平台」，而不是一套应用通吃——这是本项目最需要先和产品确认的架构分歧点。
2. **【事实】飞书必须同时建模两套 token**：`tenant_access_token`（应用身份，后台换、无用户参与）与 `user_access_token`（用户身份，OAuth 授权码 + refresh）。**【推断】**「连接」语义应对齐 `user_access_token`（谁授权就以谁的身份读文档），租户级后台同步任务才用 `tenant_access_token`。
3. **【事实】飞书事件回调 3 秒超时、最多重试 4 次、语义为「至少一次」**（<https://open.feishu.cn/document/server-docs/event-subscription-guide/overview.md>）。**【推断】** 消费端必须「先 ACK 后异步处理 + 按 `event_id` 幂等」，不能把外部 API 调用放在回调请求里。
4. **【事实】飞书 scope 未在开发者后台申请就到授权页会报 `20027`；权限范围、事件订阅、应用功能的任何变更都必须「创建新版本 → 企业管理员审核通过」才生效**（<https://open.feishu.cn/document/best-practices/intro-to-custom-app-review.md>）。**【推断】** 权限迭代周期是**天级**，不能把 scope 变更当成发个 PR 就生效的事。
5. **【事实】Notion OAuth token 绑定「一个 workspace + 一个授权用户」**，集成不能跨工作区复用，多工作区必须每个工作区各装一次（<https://developers.notion.com/docs/authorization.md>）。**【推断】** 飞书「一个连接 = 一个租户的应用凭证」与 Notion「一个连接 = 一个用户在一个 workspace 的授权」语义不同，连接表设计要给 `tenant_key`/`workspace_id` 与 `owner_user` 各留字段。
6. **【事实】两家在分页/限流/webhook 语义上差异极大**：飞书 `page_token` + 限流 429 + `x-ogw-ratelimit-reset`、webhook 至少一次；Notion `start_cursor/next_cursor` + 默认 180 req/min（非企业版）、webhook **at-most-once** 且指数退避最多 8 次至 24 小时（<https://developers.notion.com/reference/request-limits.md>、<https://developers.notion.com/reference/webhooks.md>）。**【推断】** 适配层不能共用一套重试/分页基类，只共用「连接与凭证加密」基座。

## 1. 飞书（Lark）开放平台

### 1.1 自建应用 vs 商店应用（ISV）

| 维度                     | 企业自建应用                    | 商店应用（ISV）                                                                                                                                                                                                                                        |
| ------------------------ | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 开发者主体               | 企业内部人员或授权开发者        | 第三方 ISV（**仅 ISV 资质主体可创建**）【事实】<https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview.md>                                                                                                                |
| 分发范围                 | 仅同企业内发布使用              | 上架飞书应用中心 `app.feishu.cn`，所有飞书企业可安装【事实】<https://open.feishu.cn/document/home/app-types-introduction/overview.md>                                                                                                                  |
| 事件长连接               | **支持**                        | **不支持**（只能配 Webhook 请求地址）【事实】同长连接文档                                                                                                                                                                                              |
| 工作台小组件             | 支持                            | **不支持**（其余已有能力均支持）【事实】应用类型文档                                                                                                                                                                                                   |
| 取 `tenant_access_token` | App ID + App Secret 直接换      | 先 `app_ticket`（每 1 小时推送 app_ticket 事件，可调 `app_ticket_resend` 重推）→ `app_access_token` → 用 `tenant_key` 换 `tenant_access_token`【事实】<https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/get-access-token.md> |
| 适用本仓库               | **是**（单客户/私有化语义清晰） | 否（需 ISV 资质 + 上架审核，且失去长连接）                                                                                                                                                                                                             |

**【推断】** 若产品要做成「SaaS 多租户、客户自助接入飞书」，长期只有 ISV 商店应用能免去「每个客户手工建自建应用 + 交密钥」；但那需要 ISV 资质、上架审核，且放弃长连接改 Webhook（要公网 HTTPS + 验签解密）。建议：**第一版写死自建应用**，把 ISV 模式作为后续分支在配置里预留字段。

> 应用形态（与上架方式正交）：机器人 Bot / 网页应用 H5 / 小组件 Block（云文档小组件、多维表格插件、工作台小组件）；另支持链接预览、移动应用登录。三种形态均支持服务端 API 与商店应用。

### 1.2 三套访问凭证

| 凭证                  | 身份 | 形态                                                       | 有效期                               | 获取方式                                                                          | 用途                                                                                  |
| --------------------- | ---- | ---------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `tenant_access_token` | 应用 | 前缀 `t-`（如 `t-24b5bf4e00b2af1234`）                     | 接口返回有效期，约 2h【事实】        | 自建：App ID+Secret 直接换；商店：`app_ticket`→`app_access_token`→`tenant_key` 换 | 不操作他人数据、后台同步、发机器人消息                                                |
| `user_access_token`   | 用户 | 新版为 JWT（`eyJhbGciOiJFUzI1NiIs...`），历史版本前缀 `u-` | 「两小时左右」；配套 `refresh_token` | OAuth 2.0 授权码流程                                                              | 以用户身份读写其文档/云空间                                                           |
| `app_access_token`    | 应用 | 前缀 `a-` 或 `t-`                                          | 短期                                 | 自建 App ID+Secret；商店用 app_ticket                                             | 一般仅用于商店应用换 `tenant_access_token`；**平台正逐步与 tenant_access_token 统一** |

**【事实】** 用法：HTTP 头 `Authorization: Bearer <token>`；**禁止在客户端使用**；凭证过期前重新获取会拿到相同值，仅剩余有效时间变化（<https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/get-access-token.md>）。

**【事实】** 选择原则：不操作他人数据用 `tenant_access_token`（若要读通讯录还需额外配「应用通讯录权限范围」）；需在用户个人空间创建文档等用 `user_access_token`。

**【推断】实现要点**：

- 两个 token 都要缓存（进程内或 Redis）：`tenant_access_token` 按 `app_id` 缓存、提前 5 分钟刷新、并发刷新必须单飞（single-flight），否则会把彼此换出的新 token 互相覆盖；
- `refresh_token` **只能使用一次**，刷新后旧的立即失效 → 刷新必须**按 connection 行加锁串行**，多副本部署下要落到数据库行锁或分布式锁，不能用进程内锁；
- 落库长度按 4KB 规划（官方建议 token 预留 4KB）。

### 1.3 OAuth 2.0 授权码流程（`user_access_token`）

**四步（【事实】<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/authen-v1/login-overview.md>）**：获取登录授权码 `code` → 换 `user_access_token` → 取用户身份信息（`GET /authen/v1/user_info`）→ 刷新 token。

前置准备【事实】：开发者后台「安全设置」配置重定向 URL（可配多个，**仅列表内的 URL 能通过安全校验**）。

| 步骤                     | 端点                                                           | 方法与要点                                                                                                                                                                                                                                                              |
| ------------------------ | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 授权页                   | `GET https://accounts.feishu.cn/open-apis/authen/v1/authorize` | 浏览器跳转；`code` 有效期 **5 分钟、只能用一次**；限流 1000/min、50/s【事实】<https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code.md>                                                                                                         |
| 换 token（v2，历史版本） | `POST https://open.feishu.cn/open-apis/authen/v2/oauth/token`  | `grant_type=authorization_code`；`Content-Type: application/json; charset=utf-8`；遵循 RFC 6749，可用标准 OAuth 库                                                                                                                                                      |
| 换 token（v3，迁移目标） | `POST https://accounts.feishu.cn/oauth/v3/token`               | 参数与响应同 v2；**v3 修正了 v2 的 PKCE 兼容缺陷**（v2 授权时未传 `code_challenge` 但换 token 时传 `code_verifier` 不会拒绝，v3 会拒绝）【事实】<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token.md> |
| 刷新                     | 同上端点，`grant_type=refresh_token`                           | 见下                                                                                                                                                                                                                                                                    |
| 用户信息                 | `GET https://open.feishu.cn/open-apis/authen/v1/user_info`     | 特殊频控；无需权限即可调；手机号/邮箱/employee_id 属**字段权限**，未开对应 scope 不返回【事实】<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/authen-v1/user_info/get.md>                                                                             |

**授权 URL 参数（【事实】）**：

| 参数                                       | 必填 | 说明                                                                                                                                                                 |
| ------------------------------------------ | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `client_id`                                | 是   | 应用 App ID，如 `cli_a5d611352af9d00b`                                                                                                                               |
| `response_type`                            | 是   | 固定 `code`                                                                                                                                                          |
| `redirect_uri`                             | 是   | 需 URL 编码，且必须在后台「安全设置」列表内                                                                                                                          |
| `scope`                                    | 否   | **空格分隔、区分大小写**（如 `contact:contact bitable:app:readonly`）；未在后台申请会报 `20027`；一次最多 **200** 个；**要 `refresh_token` 必须带 `offline_access`** |
| `state`                                    | 否   | 防 CSRF，**必须校验回调前后一致**                                                                                                                                    |
| `code_challenge` / `code_challenge_method` | 否   | PKCE；`S256` 推荐，`plain` 为默认                                                                                                                                    |
| `prompt`                                   | 否   | `consent` 强制重新授权                                                                                                                                               |

回调：成功 `redirect_uri?code=...&state=...`（`code` 字符集 `[A-Za-z0-9-_]`，**至少预留 64 位**）；失败 `redirect_uri?error=access_denied&state=...`。

**换 token 请求体（【事实】）**：`grant_type`、`client_id`、`client_secret`、`code`、`redirect_uri`（可选）、`code_verifier`（PKCE 时必填，43~128 字符，字符集 `A-Za-z0-9-._~`）、`scope`（可选，**仅用于缩减**：重复报 `20067`、超出已授权范围报 `20068`、多次缩减不叠加）。
**响应**：`access_token`、`refresh_token`、`refresh_token_expires_in`、`expires_in`、`scope` 等；**`refresh_token` 仅当已授权 `offline_access` 才返回**。

**refresh_token 策略（【事实】<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token.md>）**：

- 前置：后台「权限管理」开通 `offline_access` **且授权链接 scope 必须拼接 `offline_access`**；后台「安全设置」打开刷新开关（看不到则该开关默认开启）；改完**需发布应用生效**。
- `refresh_token` **仅能使用一次**，刷新返回新 `refresh_token`，原 `refresh_token` 立即失效；原 `user_access_token` 到期前仍可用。
- **365 天强制重授权**：用户授权满 365 天后必须重新走授权；`refresh_token` 到期后继续刷新报 `20037`。

**错误码速查【事实】**：`20010` 用户无应用使用权限；`20027` scope 未在后台申请；`20037` refresh_token 过期；`20067` scope 重复；`20068` scope 超出已授权范围。

**【推断】** 建议直接用 **v3 端点**（`accounts.feishu.cn/oauth/v3/token`），一次性避开 v2 的 PKCE 兼容坑；`code_verifier` 存服务端会话，不要塞进前端。

### 1.4 权限（scope）与管理员审批

- **【事实】三个维度**：访问凭证 `access_token`（虚拟身份）、**API 权限 Scope（以应用为维度授予，多个应用共用同一接口需各自申请）**、可用范围（哪些用户可见可用）。另有**数据权限**：行权限（可访问哪些用户的数据范围）、列权限（可访问哪些字段），在开发者后台申请、由**租户管理员审批**（<https://open.feishu.cn/document/home/introduction-to-scope-and-authorization/overview.md>）。
- **【事实】数据权限只对应用身份（`tenant_access_token`）生效**；申请通讯录/组织架构/任务/邮箱/飞书人事（企业版）/妙记/Payroll 的部分 API 权限时必须配，且**发布应用并等审核通过后才生效**，否则调 API 直接返回权限错误。通讯录默认范围 = 应用可用范围；组织架构默认未配置（只支持「指定员工」范围）；商店应用的数据权限只能由企业管理员配，开发者可调 `contact-v3/scope/list` 查（<https://open.feishu.cn/document/home/introduction-to-scope-and-authorization/configure-app-data-permissions.md>）。
- **【事实】发布审核**：应用的基本信息、**权限范围**、应用功能、**事件订阅**变更都需创建新版本发布，且需**企业管理员审核通过**才生效。路径：应用发布 → 版本管理与发布 → 创建版本（版本号需高于上一版）→ 填更新说明/可用范围/申请理由 → 申请线上发布。管理员可在管理后台「工作台 → 应用审核」同意/拒绝/加签，或走飞书审批自定义多轮流程；也可对具体应用开启**免审**（<https://open.feishu.cn/document/best-practices/intro-to-custom-app-review.md>）。
- **【事实】字段权限**：部分接口的敏感字段（手机号、邮箱、user_id、人员字段中的用户信息等）**只有单独开通对应 scope 才会返回**，否则该字段缺失但不报错。

**【推断】** 对连接器的影响：scope 集合必须是**产品配置**而不是代码常量——扩 scope 意味着客户侧重新发版 + 管理员审批，无法静默升级。建议在 `connector/core/config.py` 放 `FEISHU_SCOPES` 白名单，并在连接详情回显「已授予 scope vs 期望 scope」，便于产品提示重新授权。

### 1.5 事件订阅

| 维度         | 长连接（WebSocket）                                             | Webhook（发送到开发者服务器）                                                  |
| ------------ | --------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| 是否需公网   | 否                                                              | **是**，且必须是 **IPv4 公网地址**                                             |
| 支持应用类型 | **仅企业自建应用**                                              | 自建 + 商店                                                                    |
| 安全校验     | 内置加密鉴权，**事件是明文，无需解密验签**                      | 签名校验 `X-Lark-Signature` 或 Verification Token；配 Encrypt Key 时事件体加密 |
| 超时         | 3 秒内处理完且处理函数不抛异常                                  | 3 秒内返回 HTTP 200（TCP 建联超时 2 秒）                                       |
| 连接数限制   | **每应用最多 50 个**（每初始化一个 client 算一个）              | 每个应用**只能配一个请求地址**                                                 |
| 投递         | **集群模式不广播**：多客户端时只有随机一个收到                  | 全部发到唯一地址                                                               |
| 重试         | 超时/异常按 **15 秒 → 5 分钟 → 1 小时 → 6 小时**，**最多 4 次** | 同左                                                                           |

来源：【事实】<https://open.feishu.cn/document/server-docs/event-subscription-guide/overview.md>、<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/long-connection-mode.md>、<https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/choose-a-subscription-mode/send-notifications-to-developers-server.md>

**Webhook 握手（challenge）【事实】**：保存请求地址时飞书发 `application/json;charset=utf-8` 的 POST，带 `challenge`，应用须 **1 秒内原样返回** `challenge` 值（注意与事件回调的 3 秒不同）。未配 Encrypt Key 时明文 `{"challenge":"...","token":"<Verification Token>","type":"url_verification"}`。

**Webhook 签名【事实】**：`X-Lark-Signature` = `SHA256(X-Lark-Request-Timestamp + X-Lark-Request-Nonce + encrypt_key + raw_body)`，**必须用原始 body 字节**，不能反序列化再序列化。事件解密：AES-256-CBC，`key = SHA256(Encrypt Key)`，PKCS7 Padding，16 字节随机 IV，密文 = `base64(iv + encrypted_event)`，请求体形如 `{"encrypt":"..."}`（<https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/encrypt-key-encryption-configuration-case.md>）。

**订阅身份【事实】**：应用身份（`tenant_access_token`）vs **用户身份（`user_access_token`）**。云文档事件两种都支持；**日历事件只支持用户身份；邮箱事件只支持用户身份（内测中）**。

**事件结构【事实】**：v1.0 = `ts/uuid/token/type=event_callback/event`（用 `event.type` 判类型），无 `schema`；v2.0（推荐）= `schema:"2.0"` + `header.event_id/header.token/header.create_time/header.event_type/header.tenant_key/header.app_id` + `event`。**同一事件同时订阅 v1.0 与 v2.0 会收到两条重复事件。**

**语义【事实】**：「至少一次」——成功接收也可能重复，必须幂等（v1.0 用 `uuid`，v2.0 用 `event_id`）；部分事件是「有序事件」（前一事件消费成功才推下一事件）。

**【推断】实现建议**：自建应用 + 长连接明显更划算（无公网、无验签、无解密、无 IP 白名单）；但**长连接每应用上限 50、且集群不广播** → 多副本部署时只有一个副本收到事件，事件处理必须「收到后落库/入队」而不是只在内存里处理，否则副本漂移会丢。若必须有确定投递，仍要走 Webhook + 公网入口。

### 1.6 应用能力与常用端点

**【事实】** 所有端点前缀 `https://open.feishu.cn/open-apis`；分页统一 `page_token`（首页可不传）+ `page_size` + `has_more`/`next page_token`，但并非所有接口都分页（如 sheets 工作表查询不分页）。

| 能力               | 端点                                                               | 权限 scope（任选其一）                                                                                                 | 限流                                        | 备注                                                                                                                         |
| ------------------ | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 创建文档           | `POST /docx/v1/documents`                                          | `docx:document`、`docx:document:create`                                                                                | **特殊频控**                                | body `folder_token`（可选；tenant 身份时只能指定应用创建的文件夹）、`title`（1~800 字符纯文本）；支持 tenant/user 两种 token |
| 读文档纯文本       | `GET /docx/v1/documents/:document_id/raw_content`                  | `docx:document` / `docx:document:readonly`                                                                             | —                                           | 拿「文本」，不含结构                                                                                                         |
| 读文档所有块       | `GET /docx/v1/documents/:document_id/blocks`                       | `docx:document` / `docx:document:readonly`                                                                             | **5 次/秒/应用**（超限返回 400 + 99991400） | 分页；需要完整结构时**递归取子块**                                                                                           |
| 知识空间列表       | `GET /wiki/v2/spaces`                                              | `wiki:wiki` / `wiki:wiki:readonly`                                                                                     | —                                           | `page_token`/`page_size`                                                                                                     |
| 知识空间节点       | `GET /wiki/v2/spaces/:space_id/nodes`                              | 同上                                                                                                                   | —                                           | 分页；wiki 节点底层指向 docx/sheet/bitable                                                                                   |
| 多维表格表列表     | `GET /bitable/v1/apps/:app_token/tables`                           | `bitable:app` / `bitable:app:readonly`                                                                                 | —                                           | 分页                                                                                                                         |
| 多维表格记录搜索   | `POST /bitable/v1/apps/:app_token/tables/:table_id/records/search` | `base:record:retrieve` / `bitable:app` / `bitable:app:readonly`                                                        | **20 次/秒**                                | `page_size` 最大 **500**、默认 20；body 支持 `view_id`/`filter`/`sort`；人员字段等敏感值需额外 field scope                   |
| 电子表格工作表查询 | `GET /sheets/v3/spreadsheets/:spreadsheet_token/sheets/query`      | `sheets:spreadsheet` 系列                                                                                              | —                                           | **无分页**，一次性返回                                                                                                       |
| 云空间文件列表     | `GET /drive/v1/files`                                              | `drive:drive` / `drive:drive:readonly`                                                                                 | —                                           | `page_token` 分页                                                                                                            |
| 发送消息           | `POST /im/v1/messages`                                             | 应用身份：`im:message` / `im:message:send_as_bot` / `im:message:send`；用户身份：`im:message` + 「以用户身份发送消息」 | 1000/min 且 50/s                            | **查询参数 `receive_id_type` 必填**（`open_id`/`union_id`/`user_id`/`email`/`chat_id`）                                      |
| 读消息列表         | `GET /im/v1/messages`                                              | `im:message:readonly` 等                                                                                               | —                                           | 分页                                                                                                                         |
| 用户信息           | `GET /authen/v1/user_info`                                         | 无需权限；字段级另有要求                                                                                               | 特殊频控                                    | 手机号/邮箱/user_id 需 field scope                                                                                           |

文档路径：`server-docs/docs/docs/docx-v1/document/create.md`、`.../raw_content.md`、`server-docs/docs/docs/docx-v1/document/list.md`（标题「获取文档所有块」）、`server-docs/docs/wiki-v2/space/list.md`、`server-docs/docs/wiki-v2/space-node/list.md`、`.../bitable-v1/app-table/list.md`、`.../bitable-v1/app-table-record/search.md`、`server-docs/docs/sheets-v3/spreadsheet-sheet/query.md`、`server-docs/docs/drive-v1/folder/list.md`、`server-docs/im-v1/message/create.md`、`server-docs/im-v1/message/list.md`、`uAjLw4CM/ukTMukTMukTM/reference/authen-v1/user_info/get.md`。

> 【校正】早先推测 `GET /docx/v1/documents/:document_id/blocks` 缺文档页；实测 `server-docs/docs/docs/docx-v1/document/list.md` 就是该端点（标题「获取文档所有块」），**该端点为事实、非推断**。

### 1.7 限流与重试

**【事实】**（<https://open.feishu.cn/document/ukTMukTMukTM/uUzN04SN3QjL1cDN.md>）：

- 超限返回 **HTTP 429**，body `{"code":99991400,"msg":"request trigger frequency limit"}`，响应头 `x-ogw-ratelimit-limit`（窗口上限，秒）、`x-ogw-ratelimit-reset`（恢复周期，秒）。**部分旧版 API 返回 HTTP 400 而非 429**（如 docx blocks）。
- 正确姿势：读 `x-ogw-ratelimit-reset` 等待后再重试，直到成功。
- 维度通常是「每 API × 每应用 × 每租户」；自建应用档位随企业套餐不同；商店应用与所属企业无关。
- 等级表节选：1=10 次/分；2=20 次/分；3=100 次/分；4=1000 次/分且 50 次/秒；5=1 次/秒；6=5 次/秒；7=10 次/秒；8=20 次/秒；9=50 次/秒；10=基础 50/商业 100 次/秒；11=100 次/秒；21=3 次/秒；**特殊频控需联系技术支持**（docx 创建文档即属此类）。
- 自定义机器人：单租户单机器人 100 次/分钟、5 次/秒，避开 10:00、17:30 等整点/半点（否则可能 `11232`）。
- 消息、群组、多维表格**暂不支持提频**。

**【推断】** 适配层需要「按 (provider, api, tenant) 三元组」的令牌桶 + 读 `x-ogw-ratelimit-reset` 的动态退避；不要用固定 1s 重试。

### 1.8 安全

- **【事实】IP 白名单**：开发者后台可为应用设置 IP 白名单，**仅当发起请求的源 IP 在白名单内，飞书才响应，否则拒绝**（<https://open.feishu.cn/document/ukTMukTMukTM/ucTMxYjL3ETM24yNxEjN>，见 <https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview.md> 第 5 步）。
- **【事实】** 签名校验（`X-Lark-Signature`）与 AES-256-CBC 解密（见 1.5）；Verification Token 校验弱，未配 Encrypt Key 时 token 明文传输有泄露风险。
- **【事实】** 凭证禁止在客户端使用；`app_secret`/`encrypt_key` 只能服务端持有。

**【推断】** 白名单是「出站」安全（我们调飞书），与「入站」Webhook 校验正交；若部署在出口 IP 会变化的平台（Serverless/Vercel），**IP 白名单不可用**——这也是选长连接而非 Webhook 的又一个理由。

## 2. Notion

### 2.1 集成类型与 token

**【事实】** 三类凭据（<https://developers.notion.com/docs/authorization.md>、`guides/connections-and-tokens/*`）：

| 类型                           | 身份               | 安装范围                                                                           | 页面授权                                                       | 有效期                        |
| ------------------------------ | ------------------ | ---------------------------------------------------------------------------------- | -------------------------------------------------------------- | ----------------------------- |
| internal connection            | 自己的 bot 用户    | 单个工作区                                                                         | 用户在 Notion 里「Add connections」或 portal 的 Content access | 静态 token（可 Refresh 轮换） |
| personal access token（PAT）   | 创建者本人         | 单工作区                                                                           | 同上                                                           | **最长 1 年**（到期日可选）   |
| **OAuth / public integration** | **授权的那位用户** | **任意工作区**（创建时 Installation scope 必须选 Any workspace，**创建后不可改**） | 授权时用户在 page picker 里选                                  | 见 §2.2                       |

- public integration 可（非必须）上架 Marketplace，需 Notion 安全审核；**OAuth integration（scoped）**只能装进指定工作区、不能上架。
- **同一工作区多个成员要用同一 OAuth 集成，每人必须各自走一遍授权**（token 绑定用户）。
- **【事实】连接被加到某页面后即可访问该页面的所有子级**（不是逐页授权）。

### 2.2 OAuth 流程

**【事实】**（<https://developers.notion.com/guides/authorization/overview.md>）

| 步骤     | 端点                                              | 要点                                                                                                                                                                                                                                                            |
| -------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 授权页   | `GET https://api.notion.com/v1/oauth/authorize`   | 必填 `client_id`、`redirect_uri`、`response_type=code`、**`owner=user`（固定值）**；`state` 可选防 CSRF                                                                                                                                                         |
| 换 token | `POST https://api.notion.com/v1/oauth/token`      | **HTTP Basic Auth**：`CLIENT_ID:CLIENT_SECRET` base64 放 `Authorization` 头；body `grant_type=authorization_code`、`code`、`redirect_uri`（条件必填：授权 URL 带了 redirect_uri 或配了多个 redirect_uri 时必填；只配一个且授权 URL 未带时**该字段不允许出现**） |
| 刷新     | `POST https://api.notion.com/v1/oauth/token`      | body `grant_type=refresh_token` + `refresh_token`，返回新的 access + refresh【事实】<https://developers.notion.com/reference/refresh-a-token.md>                                                                                                                |
| 自省     | `POST https://api.notion.com/v1/oauth/introspect` | body `{token}` → `active`(bool)、`scope`、`iat`、`request_id`；可用于校验存量 token 是否仍有效【事实】<https://developers.notion.com/reference/introspect-token.md>                                                                                             |

**换 token 响应字段【事实】**（<https://developers.notion.com/reference/create-a-token.md>）：`access_token`、`token_type="bearer"`、`refresh_token: string|null`、`bot_id`(uuid)、`workspace_icon`、`workspace_name`、`workspace_id`(uuid)、`owner`（user 或 workspace，含 `person.email`/`email_verified`）。

- **【事实】** `/v1/oauth/*` **故意不返回 CORS 头**：换 token 必须带 client secret，只能服务端做。
- **【不确定】** 响应 schema 里**没有 `expires_in`**，官方文档也未给 access_token 的生命周期；只能确定「OAuth 现已支持 refresh_token」。**有效期时长未能联网核实**。实现上按「未知 TTL」处理：遇 401 就尝试 refresh 一次，失败则标记连接 `expired` 并要求重新授权；用 `/v1/oauth/introspect` 做周期性健康检查。

### 2.3 能力（capabilities）

**【事实】**（<https://developers.notion.com/reference/capabilities.md>）：

| 类别        | 取值                                                          | 语义                                                                                                           |
| ----------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 内容        | Read content / Update content / Insert content                | 三者可任意组合。**Insert 只给「建」不给「读/改」**（能 Create a page 但不能 Update）；**Update 不授予 Create** |
| 评论        | Read comments / Insert comments                               | Create comment 可评论 page/block 或回复已有 discussion                                                         |
| 用户信息    | 无 / 不含邮箱 / 含邮箱                                        | 控制 User 对象是否有 name/avatar/email，并决定能否 List/Retrieve users                                         |
| Agent（新） | View sessions and interact with agents / Edit selected agents | Notion Agent APIs / Custom Agents                                                                              |

**【事实】** public connection 的 capabilities 变更后，**用户需要重新授权**。

### 2.4 端点

**【事实】** Base URL `https://api.notion.com`；必带头 `Authorization: Bearer <token>` + **`Notion-Version: <日期>`**。最新版本示例值为 **`2026-03-11`**（文档多处示例使用；versioning 页把版本号渲染成占位符）。历史版本如 `2021-05-13`（`text`→`rich_text`）。

| 能力                   | 端点                                                      | 备注                                                                                                                                                                                                                                        |
| ---------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 搜索                   | `POST /v1/search`                                         | 返回连接可访问的 **pages 与 data sources**（不是旧概念 databases）；`query` 省略则不做标题过滤；`filter` = `property:object` + `value:page` 或 `value:data_source`；`filter.in_trash=true` 搜回收站；**搜索结果不能当作完整清单**；游标分页 |
| 创建页面               | `POST /v1/pages`                                          | 可指定父页面或父 data source                                                                                                                                                                                                                |
| 读页面                 | `GET /v1/pages/{page_id}`                                 | 读 `properties`                                                                                                                                                                                                                             |
| 改页面属性             | `PATCH /v1/pages/{page_id}`                               | 改 `properties`/`icon`/`cover`                                                                                                                                                                                                              |
| 单属性                 | `GET /v1/pages/{page_id}/properties/{property_id}`        | —                                                                                                                                                                                                                                           |
| 用 markdown 写页面内容 | 见 `reference/update-page-markdown.md`                    | 插入或替换页面内容（对 LLM 写入最省事）                                                                                                                                                                                                     |
| 读块                   | `GET /v1/blocks/{block_id}`                               | 单块                                                                                                                                                                                                                                        |
| 读子块                 | `GET /v1/blocks/{block_id}/children`                      | 分页；**完整内容需递归取子块**                                                                                                                                                                                                              |
| 查数据源               | `POST /v1/data_sources/{data_source_id}/query`            | 另有约 **3 请求/秒** 的单独限流                                                                                                                                                                                                             |
| 读数据源               | `GET /v1/data_sources/{id}`                               | —                                                                                                                                                                                                                                           |
| 建库                   | `POST /v1/databases`                                      | 注意 database 与 data source 是**两个对象**                                                                                                                                                                                                 |
| 读库                   | `GET /v1/databases/{id}`                                  | —                                                                                                                                                                                                                                           |
| 建评论                 | `POST /v1/comments`                                       | 评论 page/block 或回复已有 discussion                                                                                                                                                                                                       |
| 列评论                 | `GET /v1/comments`                                        | 取未解决评论                                                                                                                                                                                                                                |
| 文件上传               | `POST /v1/file_uploads` + send/complete/retrieve/list     | 对应 webhook 事件 `file_upload.created/completed/expired/failed`                                                                                                                                                                            |
| 用户                   | `GET /v1/users`、`GET /v1/users/{id}`、`GET /v1/users/me` | `me` 在只有评论能力时也能调，用于拿自身 bot id                                                                                                                                                                                              |

**【事实】版本语义**（<https://developers.notion.com/reference/versioning.md>）：只有**不兼容变更**才发新版本；加性变更（新端点/新可选参数/新响应字段）**同时作用于所有版本**（pin 版本不能推迟加性变更）。`url`/`href` 链接格式、分页 cursor、请求 id **都不是稳定标识、禁止解析**；只有 page/database/user/block id 是稳定 UUID。

### 2.5 限流

**【事实】**（<https://developers.notion.com/reference/request-limits.md>）：

- **每 connection（60 秒窗口）**：Business/Enterprise **600 请求/分钟**（均 10/秒）；**其他所有套餐 180 请求/分钟**（均 3/秒）。窗口内可任意节奏（均匀或突发）。
- **每 workspace**：跨该工作区所有 connection 共享，按套餐缩放；`Retry-After` 可能超过 1 分钟。
- **Query 一个 data source**：单独约 **3 请求/秒/connection**。
- 超限：**HTTP 429** + error code `rate_limited`，`additional_data.rate_limit_reason` ∈ `public_api_request_rate_limit`（本连接太快）、`public_api_space_request_rate_limit`（工作区共享预算耗尽）、`public_api_endpoint_rate_limit`、`mcp_tool_rate_limit`、`collection_router_too_many_requests`、`collection_router_upstream_429`、`public_api_request_blocked`。处理：读 `Retry-After` 等待。

**【推断】** 因为 workspace 级预算是**跨连接共享**的（同一客户多个用户各连一次会共享），适配层必须支持「按 workspace 汇总的限流」，否则单连接守规矩也会互相打爆。

### 2.6 Webhook

**【事实】**（<https://developers.notion.com/reference/webhooks.md>、`webhooks-events-delivery.md`）：

- **已 GA**。每个 connection 在 Developer portal 的 **Webhooks** 标签页创建订阅，填**公网 HTTPS(SSL) URL**（**localhost 不可达**），选事件类型。**没有「按用户/按页面」的订阅维度，订阅属于 connection**。
- **验证**：创建后 Notion 向该 URL 发一次性 POST，body `{"verification_token":"secret_..."}`；把 token 粘回 portal 点 Verify 才激活。**验证通过后不能改 URL**（只能删了重建），事件类型可随时改。
- **签名**：每个 webhook 请求带 **`X-Notion-Signature`**，值为 `sha256=<hex>`，是「用 `verification_token` 作密钥对**原始 body** 做 HMAC-SHA256」。必须用原始字节校验，**重新序列化会失败**。JS SDK `@notionhq/client` v5.23.0+ 提供 `verifyWebhookSignature()`。
- **投递语义：目标为 at-most-once（至多一次）**；若端点未确认收到，会**指数退避重试最多 8 次**，最后一次约在初始触发后 **24 小时**。事件**可能乱序、可能不是最新状态** → 建议凭 `entity.id` 再调 API 取最新数据。
- **payload**：`id`、`timestamp`、`workspace_id`、`workspace_name`、`subscription_id`、`integration_id`、`type`、`authors[]`、`attempt_number`、`entity{id,type}`、`data{...}`。类型如 `page.created`、`page.content_updated`、`page.deleted/undeleted`、`page.locked`、`page.properties_updated`、`database.*`、`comment.created`。
- **事件不含变更后的完整内容**，只是「变了」的信号。

**【推断】** 与飞书的「至少一次」相反，Notion 是「至多一次」→ **丢失是设计内的**，所以不能靠 webhook 保证一致性，必须有周期性 reconcile（按 `last_edited_time` 拉取）。

### 2.7 与租户/工作区的关系

**【事实/综合】** 一个 OAuth token 绑定 **一个 `workspace_id` + 一个授权用户（`owner`）**；同一 Notion 账号在不同应用/工作区的 token 互不相同；集成不能跨工作区复用一个 token；要在多工作区使用必须每个工作区各装一次（public integration 即为此设计）。

**【推断】** 对本仓库：`connector/models/connections.py` 的 `provider`/`account` 唯一约束若按「一个账号一条连接」，Notion 侧必须把 `workspace_id` 与 `owner_user_id` 一起纳入唯一键，否则同一 Notion 账号在 A/B 两个工作区的授权会互相覆盖。

## 3. 飞书连接器最小可用能力清单

分层：**P0 = 第一版必须**（不做则连接器无意义）、**P1 = 三个月内**、**P2 = 看需求**。

| 层  | 能力               | 端点                                                       | 所需 scope                                       | 身份              | 读写 | 备注                                                                                                                              |
| --- | ------------------ | ---------------------------------------------------------- | ------------------------------------------------ | ----------------- | ---- | --------------------------------------------------------------------------------------------------------------------------------- |
| P0  | 发起授权           | `GET accounts.feishu.cn/open-apis/authen/v1/authorize`     | —                                                | —                 | —    | `offline_access` 必须拼进 scope；`state` 必校验；PKCE(S256)                                                                       |
| P0  | 换 token           | `POST accounts.feishu.cn/oauth/v3/token`                   | —                                                | —                 | —    | 存 access + refresh（加密列）；**4KB 预留**                                                                                       |
| P0  | 刷新 token         | 同上 `grant_type=refresh_token`                            | —                                                | —                 | —    | refresh 一次性 → **按连接行加锁**；失败置 `expired` 待重授权                                                                      |
| P0  | 取授权人身份       | `GET /authen/v1/user_info`                                 | 无需                                             | user              | 读   | 存 `open_id`/`union_id` 做连接归属                                                                                                |
| P0  | 读文档纯文本       | `GET /docx/v1/documents/:id/raw_content`                   | `docx:document:readonly`                         | user（或 tenant） | 读   | 最常用的「喂给模型」入口                                                                                                          |
| P0  | 读文档块（结构）   | `GET /docx/v1/documents/:id/blocks`                        | `docx:document:readonly`                         | user              | 读   | 5 次/秒/应用；递归子块                                                                                                            |
| P0  | 列云空间文件       | `GET /drive/v1/files`                                      | `drive:drive:readonly`                           | user              | 读   | `page_token` 分页；用于「选文件」UI                                                                                               |
| P0  | 列知识空间 / 节点  | `GET /wiki/v2/spaces`、`/wiki/v2/spaces/:id/nodes`         | `wiki:wiki:readonly`                             | user              | 读   | wiki 节点需再解析到底层 docx/sheet/bitable                                                                                        |
| P0  | 撤销连接           | 本地标记 `revoked`（+ 可选调飞书侧撤销）                   | —                                                | —                 | —    | **飞书是否有官方 revoke 端点未能核实**；现状 `DELETE /v1/connections/{id}` 只做本地留痕（`services/connector-service/README.md`） |
| P1  | 创建文档           | `POST /docx/v1/documents`                                  | `docx:document`                                  | user              | 写   | **特殊频控**；tenant 身份只能落应用自己创建的文件夹                                                                               |
| P1  | 写文档内容（块）   | docx blocks 创建/批量更新                                  | `docx:document`                                  | user              | 写   | 需申请写权限 → 触发重新发版审核                                                                                                   |
| P1  | 多维表格读记录     | `POST /bitable/v1/apps/:app/tables/:table/records/search`  | `base:record:retrieve` 或 `bitable:app:readonly` | user              | 读   | page_size≤500；20 次/秒                                                                                                           |
| P1  | 电子表格读         | `GET /sheets/v3/spreadsheets/:token/sheets/query` + 读区间 | `sheets:spreadsheet:readonly`                    | user              | 读   | 无分页                                                                                                                            |
| P1  | 事件订阅（长连接） | `lark-oapi` ws.Client                                      | —                                                | 应用              | 读   | 仅自建应用；50 连接上限；3 秒内处理完                                                                                             |
| P2  | 发消息 / 机器人    | `POST /im/v1/messages`                                     | `im:message:send_as_bot`                         | tenant            | 写   | `receive_id_type` 必填                                                                                                            |
| P2  | 多维表格写         | records create/update/delete                               | `bitable:app`                                    | user              | 写   | 同上需重新审核                                                                                                                    |
| P2  | 通讯录             | `/contact/v3/*`                                            | `contact:contact:readonly`                       | tenant            | 读   | **必须额外配数据权限**（行/列范围），否则空                                                                                       |

**【推断】MVP 边界建议**：P0 全部 + 「只读」。飞书「写」能力的边际收益远低于成本——写 scope 要重新发版 + 管理员审批，且写错文档不可逆（用户信任成本高）。先把「读 + 稳定连接 + 失效可见」做扎实。

## 4. Notion 接入差异点

| 维度             | 飞书                                                             | Notion                                                                                    | 对实现的含义                                                                                   |
| ---------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 应用模型         | 自建应用（每客户一套凭证）/ ISV 商店应用                         | 一个 public integration 服务所有工作区，Installation scope 创建后不可改                   | Notion 侧 client_id/secret 是**全局配置**；飞书侧可能是**每租户配置** → 配置表要能同时表达两种 |
| 连接粒度         | 一个租户 → 一套应用凭证 + N 个用户授权                           | 一个连接 = **一个 workspace + 一个用户**                                                  | 唯一键要含 `workspace_id` + `owner_user_id`                                                    |
| token 类型       | `tenant_access_token`（应用）+ `user_access_token`（用户）       | 单一 bearer（OAuth）/ 静态（internal/PAT）                                                | 飞书要**两套缓存与刷新策略**；Notion 只需一套                                                  |
| 刷新语义         | refresh **一次性**、旧的立即失效；365 天强制重授权               | `grant_type=refresh_token` 返回新 access + 新 refresh                                     | 两边都要「按连接串行 + 单飞」，但飞书还必须处理 365 天硬到期                                   |
| TTL              | 明确（约 2h / 两小时左右）                                       | **文档未给 `expires_in`，未能核实**                                                       | Notion 侧按「未知 TTL + 401 触发 refresh + introspect 巡检」实现                               |
| 授权范围模型     | scope（后台申请 + 管理员审批）+ 数据权限（行/列）                | capabilities（4 类能力 × 3 档用户信息）+ **页面级授权**（用户自选页面）                   | 飞书是「声明式权限」，Notion 是「用户挑资源」→ 连接详情要能展示「实际可访问资源」              |
| 子资源继承       | 无此概念（按文档 token 访问）                                    | **授权一个页面即授权其全部子级**                                                          | Notion 侧「授权范围」会随用户建子页自动扩大 → 展示上要提示                                     |
| 分页             | `page_token` / `page_size` / `has_more`；部分接口不分页          | `start_cursor` / `next_cursor`，全游标                                                    | 不能共用分页基类；且 Notion cursor **不是稳定标识**                                            |
| 限流             | 429（部分旧接口 400）+ `x-ogw-ratelimit-reset`；按 API×应用×租户 | 429 + `Retry-After`；**connection 级 + workspace 级共享预算**；query data source 单独 3/s | 令牌桶维度不同；Notion 还要做「跨连接共享预算」                                                |
| webhook 语义     | **至少一次**；重试 15s/5m/1h/6h ×4；事件含内容或需再查           | **at-most-once**；重试指数退避 ×8 至 24h；事件只是「变了」的信号                          | 飞书：幂等；Notion：**必须 reconcile 兜底**                                                    |
| webhook 建立方式 | 后台配 URL（每应用一个）+ challenge 1 秒；或长连接               | **每个 connection 各自在 portal 创建订阅** + 一次性 verification_token 回填               | Notion 无法通过 API 自动建订阅 → **接入流程必须包含人工/引导步骤**                             |
| 签名             | `X-Lark-Signature` = SHA256(ts+nonce+key+raw_body)               | `X-Notion-Signature` = HMAC-SHA256(verification_token, raw_body)，前缀 `sha256=`          | 都要求**原始 body**，不能反序列化再序列化                                                      |
| 加密             | 配 Encrypt Key 时 AES-256-CBC 加密整个事件体                     | 不加密，只签名                                                                            | —                                                                                              |
| 组件/格式        | docx（块模型）、wiki、bitable、sheets、drive 各自独立 API        | 统一 block 模型（page 也是一棵树）+ data source                                           | 归一化成「文档树」的成本：Notion 低、飞书高（需按类型分派）                                    |
| API 版本         | 路径里带版本（`/docx/v1`、`/wiki/v2`、`/sheets/v3`）             | **头 `Notion-Version` 必填**，加性变更对所有版本生效                                      | Notion 必须把版本号做成配置项（不要硬编码在 provider 里）                                      |
| 撤销             | 本地标记；服务端 revoke 端点**未核实**                           | portal 可 revoke；token 暴露后应立即 revoke/replace                                       | —                                                                                              |

## 5. 落到 `connector-service` 的实现要点

对齐 `services/connector-service/README.md` 的「接入新连接器 5 步」：

1. **连接表**（`connector/models/connections.py`）：现有 `provider`/`account` 唯一约束不够——飞书要存 `app_id`（每租户）、`tenant_key`、`open_id`；Notion 要存 `workspace_id`、`owner_user_id`、`bot_id`。写进 `credentials` JSON（加密列）或新增 `metadata` JSON 列需与既有迁移策略对齐。
2. **provider 模块**（`connector/services/feishu.py`、`notion.py`）：只封装 HTTP 与错误码映射，**不碰数据库**；凭证进出全走 `encrypt_credentials`/`decrypt_credentials`，日志与响应一律脱敏。
3. **路由**（`connector/api/v1/feishu.py`）：**当前没有创建连接的端点**（README 只列了 GET/DELETE）→ 需要新增 `POST /v1/connections` + `GET /v1/oauth/{provider}/authorize` + `GET /v1/oauth/{provider}/callback`；`state` 用服务端签名或存表，不能信任前端传值。
4. **OAuth 流程**：框架未内置，需自建。要点——回调里换 token 后**先落库再重定向**（失败要能回到前端看到错误）；`refresh_token` 刷新必须按连接行加锁（用数据库行锁，不用进程锁，因为多副本）。
5. **配置**（`connector/core/config.py` + `.env.example`）：`FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_ENCRYPT_KEY`/`FEISHU_SCOPES`、`NOTION_CLIENT_ID`/`NOTION_CLIENT_SECRET`/`NOTION_VERSION`。

**【推断】** 令牌刷新应做成独立后台任务（或按需 lazy refresh + 行锁），不要塞进事件回调：飞书 3 秒超时下，一次外部 HTTP 就可能超时触发重推。

## 6. 未能核实 / 需产品与运维确认

| #   | 项                                                                | 状态                                                                            |
| --- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| 1   | **Notion access_token 的有效期**                                  | 响应 schema 无 `expires_in`，文档未写；**未能联网核实**。实现按「未知 TTL」防御 |
| 2   | **飞书是否提供官方「撤销 user_access_token」端点**                | **未能核实**；P0 先只做本地 `revoked` 标记                                      |
| 3   | 飞书 `refresh_token_expires_in` 的典型值                          | 文档只说 365 天强制重授权、过期报 `20037`；具体秒数未取到                       |
| 4   | 飞书「用户身份订阅云文档事件」的开通方式                          | 只确认了「云文档事件支持两种身份」，具体后台开关路径未读到                      |
| 5   | 飞书 docx **写块** 的具体端点                                     | 本次只核实了读（`document/list.md`）；写端点未逐页核实                          |
| 6   | 飞书 sheets **读区间** 端点                                       | 只核实了 `sheets/query`（工作表元信息），读单元格端点未逐页核实                 |
| 7   | Notion `external_account` 参数用途（OAuth 换 token 请求体里出现） | 未在文档正文找到解释，只有 schema                                               |

## 7. 主要来源

飞书：<https://open.feishu.cn/document/home/app-types-introduction/overview.md>、<https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/get-access-token.md>、<https://open.feishu.cn/document/server-docs/api-call-guide/calling-process/overview.md>、<https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code.md>、<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/authen-v1/login-overview.md>、<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token.md>、<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token.md>、<https://open.feishu.cn/document/server-docs/event-subscription-guide/overview.md>、<https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/long-connection-mode.md>、<https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/encrypt-key-encryption-configuration-case.md>、<https://open.feishu.cn/document/ukTMukTMukTM/uUzN04SN3QjL1cDN.md>、<https://open.feishu.cn/document/home/introduction-to-scope-and-authorization/overview.md>、<https://open.feishu.cn/document/home/introduction-to-scope-and-authorization/configure-app-data-permissions.md>、<https://open.feishu.cn/document/best-practices/intro-to-custom-app-review.md>、<https://open.feishu.cn/document/ukTMukTMukTM/ucTMxYjL3ETM24yNxEjN>

Notion：<https://developers.notion.com/docs/authorization.md>、<https://developers.notion.com/guides/authorization/overview.md>、<https://developers.notion.com/reference/create-a-token.md>、<https://developers.notion.com/reference/refresh-a-token.md>、<https://developers.notion.com/reference/introspect-token.md>、<https://developers.notion.com/reference/capabilities.md>、<https://developers.notion.com/reference/request-limits.md>、<https://developers.notion.com/reference/webhooks.md>、<https://developers.notion.com/reference/intro.md>、<https://developers.notion.com/reference/versioning.md>、<https://developers.notion.com/reference/post-search.md>

仓库内：`services/connector-service/README.md`、`docs/architecture.md:79`、`docs/architecture.md:116`、`docs/architecture.md:434`、`docs/architecture.md:450`。
